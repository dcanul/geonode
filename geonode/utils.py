#########################################################################
#
# Copyright (C) 2012 OpenPlans
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################

import httplib2
import base64
import re
import math
import copy

from urlparse import urlparse
from collections import namedtuple
from django.conf import settings
from django.core.exceptions import PermissionDenied, ImproperlyConfigured
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.utils import simplejson as json
from django.http import HttpResponse
from geonode.security.enumerations import AUTHENTICATED_USERS, ANONYMOUS_USERS, INVALID_PERMISSION_MESSAGE
from urlparse import urlsplit

DEFAULT_TITLE=""
DEFAULT_ABSTRACT=""

http_client = httplib2.Http()

def _get_basic_auth_info(request):
    """
    grab basic auth info
    """
    meth, auth = request.META['HTTP_AUTHORIZATION'].split()
    if meth.lower() != 'basic':
        raise ValueError
    username, password = base64.b64decode(auth).split(':')
    return username, password


def batch_permissions(request):
    """
    if not request.user.is_authenticated:
        return HttpResponse("You must log in to change permissions", status=401)

    if request.method != "POST":
        return HttpResponse("Permissions API requires POST requests", status=405)

    spec = json.loads(request.body)

    if "layers" in spec:
        lyrs = Layer.objects.filter(pk__in = spec['layers'])
        for lyr in lyrs:
            if not request.user.has_perm("maps.change_layer_permissions", obj=lyr):
                return HttpResponse("User not authorized to change layer permissions", status=403)

    if "maps" in spec:
        map_query = Map.objects.filter(pk__in = spec['maps'])
        for m in map_query:
            if not request.user.has_perm("base.change_resourcebase_permissions", obj=m):
                return HttpResponse("User not authorized to change map permissions", status=403)

    anon_level = spec['permissions'].get("anonymous")
    auth_level = spec['permissions'].get("authenticated")
    users = spec['permissions'].get('users', [])
    user_names = [x[0] for x in users]

    if "layers" in spec:
        lyrs = Layer.objects.filter(pk__in = spec['layers'])
        valid_perms = ['layer_readwrite', 'layer_readonly']
        if anon_level not in valid_perms:
            anon_level = "_none"
        if auth_level not in valid_perms:
            auth_level = "_none"
        for lyr in lyrs:
            lyr.get_user_levels().exclude(user__username__in = user_names + [lyr.owner.username]).delete()
            lyr.set_gen_level(ANONYMOUS_USERS, anon_level)
            lyr.set_gen_level(AUTHENTICATED_USERS, auth_level)
            for user, user_level in users:
                if user_level not in valid_perms:
                    user_level = "_none"
                lyr.set_user_level(user, user_level)

    if "maps" in spec:
        map_query = Map.objects.filter(pk__in = spec['maps'])
        valid_perms = ['layer_readwrite', 'layer_readonly']
        if anon_level not in valid_perms:
            anon_level = "_none"
        if auth_level not in valid_perms:
            auth_level = "_none"
        anon_level = anon_level.replace("layer", "map")
        auth_level = auth_level.replace("layer", "map")

        for m in map_query:
            m.get_user_levels().exclude(user__username__in = user_names + [m.owner.username]).delete()
            m.set_gen_level(ANONYMOUS_USERS, anon_level)
            m.set_gen_level(AUTHENTICATED_USERS, auth_level)
            for user, user_level in spec['permissions'].get("users", []):
                user_level = user_level.replace("layer", "map")
                m.set_user_level(user, valid_perms.get(user_level, "_none"))

    return HttpResponse("Not implemented yet")
    """
    pass

def batch_delete(request):
    """
    if not request.user.is_authenticated:
        return HttpResponse("You must log in to delete layers", status=401)

    if request.method != "POST":
        return HttpResponse("Delete API requires POST requests", status=405)

    spec = json.loads(request.body)

    if "layers" in spec:
        lyrs = Layer.objects.filter(pk__in = spec['layers'])
        for lyr in lyrs:
            if not request.user.has_perm("maps.delete_layer", obj=lyr):
                return HttpResponse("User not authorized to delete layer", status=403)

    if "maps" in spec:
        map_query = Map.objects.filter(pk__in = spec['maps'])
        for m in map_query:
            if not request.user.has_perm("base.delete_resourcebase", obj=m):
                return HttpResponse("User not authorized to delete map", status=403)

    if "layers" in spec:
        Layer.objects.filter(pk__in = spec["layers"]).delete()

    if "maps" in spec:
        Map.objects.filter(pk__in = spec["maps"]).delete()

    nlayers = len(spec.get('layers', []))
    nmaps = len(spec.get('maps', []))

    return HttpResponse("Deleted %d layers and %d maps" % (nlayers, nmaps))
    """
    pass

def _handle_perms_edit(request, obj):
    errors = []
    params = request.POST
    valid_pl = obj.permission_levels

    anon_level = params[ANONYMOUS_USERS]
    # validate anonymous level, disallow admin level
    if not anon_level in valid_pl or anon_level == obj.LEVEL_ADMIN:
        errors.append(_("Anonymous Users") + ": " + INVALID_PERMISSION_MESSAGE)

    all_auth_level = params[AUTHENTICATED_USERS]
    if not all_auth_level in valid_pl:
        errors.append(_("Registered Users") + ": " + INVALID_PERMISSION_MESSAGE)

    kpat = re.compile("^u_(.*)_level$")
    ulevs = {}
    for k, level in params.items():
        m = kpat.match(k)
        if m:
            username = m.groups()[0]
            if not level in valid_pl:
                errors.append(_("User") + " " + username + ": " + INVALID_PERMISSION_MESSAGE)
            else:
                ulevs[username] = level

    if len(errors) == 0:
        obj.set_gen_level(ANONYMOUS_USERS, anon_level)
        obj.set_gen_level(AUTHENTICATED_USERS, all_auth_level)

        for username, level in ulevs.items():
            user = User.objects.get(username=username)
            obj.set_user_level(user, level)

    return errors


def _split_query(query):
    """
    split and strip keywords, preserve space
    separated quoted blocks.
    """

    qq = query.split(' ')
    keywords = []
    accum = None
    for kw in qq:
        if accum is None:
            if kw.startswith('"'):
                accum = kw[1:]
            elif kw:
                keywords.append(kw)
        else:
            accum += ' ' + kw
            if kw.endswith('"'):
                keywords.append(accum[0:-1])
                accum = None
    if accum is not None:
        keywords.append(accum)
    return [kw.strip() for kw in keywords if kw.strip()]


def bbox_to_wkt(x0, x1, y0, y1, srid="4326"):
    if None not in [x0, x1, y0, y1]:
        wkt = 'SRID=%s;POLYGON((%s %s,%s %s,%s %s,%s %s,%s %s))' % (srid,
                               x0, y0, x0, y1, x1, y1, x1, y0, x0, y0)
    else:
        wkt = 'SRID=4326;POLYGON((-180 -90,-180 90,180 90,180 -90,-180 -90))'
    return wkt

def llbbox_to_mercator(llbbox):
    minlonlat = forward_mercator([llbbox[0],llbbox[1]])
    maxlonlat = forward_mercator([llbbox[2],llbbox[3]])
    return [minlonlat[0],minlonlat[1],maxlonlat[0],maxlonlat[1]]

def mercator_to_llbbox(bbox):
    minlonlat = inverse_mercator([bbox[0],bbox[1]])
    maxlonlat = inverse_mercator([bbox[2],bbox[3]])
    return [minlonlat[0],minlonlat[1],maxlonlat[0],maxlonlat[1]]

def forward_mercator(lonlat):
    """
        Given geographic coordinates, return a x,y tuple in spherical mercator.

        If the lat value is out of range, -inf will be returned as the y value
    """
    x = lonlat[0] * 20037508.34 / 180
    try:
        # With data sets that only have one point the value of this
        # expression becomes negative infinity. In order to continue,
        # we wrap this in a try catch block.
        n = math.tan((90 + lonlat[1]) * math.pi / 360)
    except ValueError:
        n = 0
    if n <= 0:
        y = float("-inf")
    else:
        y = math.log(n) / math.pi * 20037508.34
    return (x, y)


def inverse_mercator(xy):
    """
        Given coordinates in spherical mercator, return a lon,lat tuple.
    """
    lon = (xy[0] / 20037508.34) * 180
    lat = (xy[1] / 20037508.34) * 180
    lat = 180/math.pi * (2 * math.atan(math.exp(lat * math.pi / 180)) - math.pi / 2)
    return (lon, lat)


def layer_from_viewer_config(model, layer, source, ordering):
    """
    Parse an object out of a parsed layer configuration from a GXP
    viewer.

    ``model`` is the type to instantiate
    ``layer`` is the parsed dict for the layer
    ``source`` is the parsed dict for the layer's source
    ``ordering`` is the index of the layer within the map's layer list
    """
    layer_cfg = dict(layer)
    for k in ["format", "name", "opacity", "styles", "transparent",
                "fixed", "group", "visibility", "source", "getFeatureInfo"]:
        if k in layer_cfg: del layer_cfg[k]

    source_cfg = dict(source)
    for k in ["url", "projection"]:
        if k in source_cfg: del source_cfg[k]

    return model(
        stack_order = ordering,
        format = layer.get("format", None),
        name = layer.get("name", None),
        opacity = layer.get("opacity", 1),
        styles = layer.get("styles", None),
        transparent = layer.get("transparent", False),
        fixed = layer.get("fixed", False),
        group = layer.get('group', None),
        visibility = layer.get("visibility", True),
        ows_url = source.get("url", None),
        layer_params = json.dumps(layer_cfg),
        source_params = json.dumps(source_cfg)
    )


class GXPMapBase(object):

    def viewer_json(self, *added_layers):
        """
        Convert this map to a nested dictionary structure matching the JSON
        configuration for GXP Viewers.

        The ``added_layers`` parameter list allows a list of extra MapLayer
        instances to append to the Map's layer list when generating the
        configuration. These are not persisted; if you want to add layers you
        should use ``.layer_set.create()``.
        """

        layers = list(self.layers)
        layers.extend(added_layers)

        server_lookup = {}
        sources = { }

        def uniqify(seq):
            """
            get a list of unique items from the input sequence.

            This relies only on equality tests, so you can use it on most
            things.  If you have a sequence of hashables, list(set(seq)) is
            better.
            """
            results = []
            for x in seq:
                if x not in results: results.append(x)
            return results

        configs = [l.source_config() for l in layers]

        i = 0
        for source in uniqify(configs):
            while str(i) in sources: i = i + 1
            sources[str(i)] = source
            server_lookup[json.dumps(source)] = str(i)

        def source_lookup(source):
            for k, v in sources.iteritems():
                if v == source: return k
            return None

        def layer_config(l):
            cfg = l.layer_config()
            src_cfg = l.source_config()
            source = source_lookup(src_cfg)
            if source: cfg["source"] = source
            return cfg

        source_urls = [source['url'] for source in sources.values() if source.has_key('url')]
       
        if 'geonode.geoserver' in settings.INSTALLED_APPS:
            if not settings.MAP_BASELAYERS[0]['source']['url'] in source_urls:
                keys = sources.keys()
                keys.sort()
                settings.MAP_BASELAYERS[0]['source']['title'] = 'Local Geoserver'
                sources[str(int(keys[-1])+1)] = settings.MAP_BASELAYERS[0]['source']

        def _base_source(source):
            base_source = copy.deepcopy(source)
            for key in ["id", "baseParams", "title"]:
                if key in base_source: del base_source[key]
            return base_source

        for idx, lyr in enumerate(settings.MAP_BASELAYERS):
            if _base_source(lyr["source"]) not in map(_base_source, sources.values()):
                sources[str(int(max(sources.keys(), key=int)) +1)] = lyr["source"]
                
        config = {
            'id': self.id,
            'about': {
                'title':    self.title,
                'abstract': self.abstract
            },
            'defaultSourceType': "gxp_wmscsource",
            'sources': sources,
            'map': {
                'layers': [layer_config(l) for l in layers],
                'center': [self.center_x, self.center_y],
                'projection': self.projection,
                'zoom': self.zoom
            }
        }
        
        if any(layers):
            # Mark the last added layer as selected - important for data page
            config["map"]["layers"][len(layers)-1]["selected"] = True

        config["map"].update(_get_viewer_projection_info(self.projection))
        return config


class GXPMap(GXPMapBase):

    def __init__(self, projection=None, title=None, abstract=None,
                 center_x = None, center_y = None, zoom = None):
        self.id = 0
        self.projection = projection
        self.title = title or DEFAULT_TITLE
        self.abstract = abstract or DEFAULT_ABSTRACT
        _DEFAULT_MAP_CENTER = forward_mercator(settings.DEFAULT_MAP_CENTER)
        self.center_x = center_x if center_x is not None else _DEFAULT_MAP_CENTER[0]
        self.center_y = center_y if center_y is not None else _DEFAULT_MAP_CENTER[1]
        self.zoom = zoom if zoom is not None else settings.DEFAULT_MAP_ZOOM
        self.layers = []


class GXPLayerBase(object):

    def source_config(self):
        """
        Generate a dict that can be serialized to a GXP layer source
        configuration suitable for loading this layer.
        """
        try:
            cfg = json.loads(self.source_params)
        except Exception:
            cfg = dict(ptype="gxp_wmscsource", restUrl="/gs/rest")

        if self.ows_url: cfg["url"] = self.ows_url

        return cfg

    def layer_config(self):
        """
        Generate a dict that can be serialized to a GXP layer configuration
        suitable for loading this layer.

        The "source" property will be left unset; the layer is not aware of the
        name assigned to its source plugin.  See
        geonode.maps.models.Map.viewer_json for an example of
        generating a full map configuration.
        """
        try:
            cfg = json.loads(self.layer_params)
        except Exception:
            cfg = dict()

        if self.format: cfg['format'] = self.format
        if self.name: cfg["name"] = self.name
        if self.opacity: cfg['opacity'] = self.opacity
        if self.styles: cfg['styles'] = self.styles
        if self.transparent: cfg['transparent'] = True

        cfg["fixed"] = self.fixed
        if self.group: cfg["group"] = self.group
        cfg["visibility"] = self.visibility

        return cfg


class GXPLayer(GXPLayerBase):
    '''GXPLayer represents an object to be included in a GXP map.
    '''
    def __init__(self, name=None, ows_url=None, **kw):
        self.format = None
        self.name = name
        self.opacity = 1.0
        self.styles = None
        self.transparent = False
        self.fixed = False
        self.group = None
        self.visibility = True
        self.ows_url = ows_url
        self.layer_params = ""
        self.source_params = ""
        for k in kw:
            setattr(self,k,kw[k])


def default_map_config():
    _DEFAULT_MAP_CENTER = forward_mercator(settings.DEFAULT_MAP_CENTER)

    _default_map = GXPMap(
        title=DEFAULT_TITLE,
        abstract=DEFAULT_ABSTRACT,
        projection="EPSG:900913",
        center_x=_DEFAULT_MAP_CENTER[0],
        center_y=_DEFAULT_MAP_CENTER[1],
        zoom=settings.DEFAULT_MAP_ZOOM
    )
    def _baselayer(lyr, order):
        return layer_from_viewer_config(
            GXPLayer,
            layer = lyr,
            source = lyr["source"],
            ordering = order
        )

    DEFAULT_BASE_LAYERS = [_baselayer(lyr, idx) for idx, lyr in enumerate(settings.MAP_BASELAYERS)]
    DEFAULT_MAP_CONFIG = _default_map.viewer_json(*DEFAULT_BASE_LAYERS)

    return DEFAULT_MAP_CONFIG, DEFAULT_BASE_LAYERS


_viewer_projection_lookup = {
    "EPSG:900913": {
        "maxResolution": 156543.03390625,
        "units": "m",
        "maxExtent": [-20037508.34,-20037508.34,20037508.34,20037508.34],
    },
    "EPSG:4326": {
        "max_resolution": (180 - (-180)) / 256,
        "units": "degrees",
        "maxExtent": [-180, -90, 180, 90]
    }
}


def _get_viewer_projection_info(srid):
    # TODO: Look up projection details in EPSG database
    return _viewer_projection_lookup.get(srid, {})


def resolve_object(request, model, query, permission=None,
                   permission_required=True, permission_msg=None):
    """Resolve an object using the provided query and check the optional
    permission. Model views should wrap this function as a shortcut.

    query - a dict to use for querying the model
    permission - an optional permission to check
    permission_required - if False, allow get methods to proceed
    permission_msg - optional message to use in 403
    """
    obj = get_object_or_404(model, **query)
    allowed = True
    if permission:
        if permission_required or request.method != 'GET':
            allowed = request.user.has_perm(permission, obj.get_self_resource())
    if not allowed:
        mesg = permission_msg or _('Permission Denied')
        raise PermissionDenied(mesg)
    return obj


def json_response(body=None, errors=None, redirect_to=None, exception=None,
                  content_type=None, status=None):
   """Create a proper JSON response. If body is provided, this is the response.
   If errors is not None, the response is a success/errors json object.
   If redirect_to is not None, the response is a success=True, redirect_to object
   If the exception is provided, it will be logged. If body is a string, the
   exception message will be used as a format option to that string and the
   result will be a success=False, errors = body % exception
   """
   if content_type is None:
       content_type = "application/json"
   if errors:
       if isinstance(errors, basestring):
           errors = [errors]
       body = {
           'success' : False,
           'errors' : errors
       }
   elif redirect_to:
       body = {
           'success' : True,
           'redirect_to' : redirect_to
       }
   elif exception:
       if body is None:
           body = "Unexpected exception %s" % exception
       else:
           body = body % exception
       body = {
           'success' : False,
           'errors' : [ body ]
       }
   elif body:
       pass
   else:
       raise Exception("must call with body, errors or redirect_to")
   
   if status==None:
      status = 200

   if not isinstance(body, basestring):
       body = json.dumps(body)
   return HttpResponse(body, content_type=content_type, status=status)
