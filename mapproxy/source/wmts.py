# This file is part of the MapProxy project.
# Copyright (C) 2025 Omniscale <http://omniscale.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from mapproxy.layer import MapLayer, DefaultMapExtent
from mapproxy.image.opts import ImageOptions
from mapproxy.client.tile import TileClient, TileURLTemplate
from mapproxy.grid.tile_grid import tile_grid
from mapproxy.srs import SRS


class WMTSSource(MapLayer):
    def __init__(self, url, layer, image_opts=None, tilematrixset=None, grid=None, format='png', dimensions=None):
        MapLayer.__init__(self, image_opts=image_opts)
        self.url = url
        self.layer = layer
        self.tilematrixset = tilematrixset or 'GLOBAL_MERCATOR'
        self.format = format
        self.default_dimensions = dimensions or {}
        self.extent = DefaultMapExtent()
        
        # Setup grid if not provided
        if grid is None:
            if self.tilematrixset == 'GLOBAL_MERCATOR':
                self.grid = tile_grid(3857)  # Web Mercator
            else:
                # Default to web mercator for now
                self.grid = tile_grid(3857)
        else:
            self.grid = grid

    def _build_url_template(self, dimensions=None):
        """
        Build WMTS URL template with given dimensions.
        This allows dynamic dimension handling during seeding.
        """
        # Use provided dimensions or fall back to defaults
        effective_dimensions = dimensions or self.default_dimensions
        
        # Create WMTS URL template for REST requests
        # Format: /{layer}/{tilematrixset}/{z}/{x}/{y}.{format}
        wmts_template = self.url.rstrip('/') + '/{layer}/{tilematrixset}/{z}/{x}/{y}.{format}'
        
        # Substitute fixed parameters
        wmts_template = wmts_template.replace('{layer}', self.layer)
        wmts_template = wmts_template.replace('{tilematrixset}', self.tilematrixset)
        wmts_template = wmts_template.replace('{format}', self.format)
        
        # Add dimensions as query parameters if present
        if effective_dimensions:
            # Convert dimensions dict to query string
            dimension_params = []
            for key, value in effective_dimensions.items():
                dimension_params.append(f'{key}={value}')
            if dimension_params:
                wmts_template += '?' + '&'.join(dimension_params)
        
        # Now convert to MapProxy tile template format
        # {z} -> %(z)s, {x} -> %(x)s, {y} -> %(y)s
        tile_template = wmts_template.replace('{z}', '%(z)s').replace('{x}', '%(x)s').replace('{y}', '%(y)s')
        
        return tile_template

    def get_map(self, query):
        """
        Return tiles for the given query.
        For tile-based sources, we need to request individual tiles that cover the query bbox.
        """
        # Validate grid compatibility like TiledSource does
        if self.grid.tile_size != query.size:
            from mapproxy.source import InvalidSourceQuery
            raise InvalidSourceQuery(
                'tile size of cache and tile source do not match: %s != %s'
                % (self.grid.tile_size, query.size)
            )

        if self.grid.srs != query.srs:
            from mapproxy.source import InvalidSourceQuery
            raise InvalidSourceQuery(
                'SRS of cache and tile source do not match: %r != %r'
                % (self.grid.srs, query.srs)
            )
        
        # Build URL template with dimensions from query (for seeding) or defaults
        effective_dimensions = query.dimensions if query.dimensions else self.default_dimensions
        tile_template = self._build_url_template(effective_dimensions)
        
        # Create client with the appropriate URL template for this query
        from mapproxy.client.tile import TileURLTemplate, TileClient
        url_template = TileURLTemplate(tile_template, format=self.format)
        client = TileClient(url_template, grid=self.grid)
        
        # Get tiles that intersect with the query bbox
        _bbox, grid, tiles = self.grid.get_affected_tiles(query.bbox, query.size)
        
        # For single tile requests (as in seeding), we expect exactly one tile
        if grid != (1, 1):
            from mapproxy.source import InvalidSourceQuery
            raise InvalidSourceQuery('BBOX does not align to tile')
            
        tile_coord = next(tiles)
        
        try:
            return client.get_tile(tile_coord, format=query.format)
        except Exception as e:
            import logging
            log = logging.getLogger('mapproxy.source.wmts')
            log.warning('could not retrieve WMTS tile: %s', e)
            raise
