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

from __future__ import division

import os
import pytest

from io import BytesIO

from mapproxy.compat.image import Image
from mapproxy.seed.seeder import seed
from mapproxy.seed.config import load_seed_tasks_conf
from mapproxy.config.loader import load_configuration
from mapproxy.test.helper import assert_files_in_dir
from mapproxy.test.system import SysTest
from mapproxy.test.http import MockWMTSServ

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixture')

@pytest.fixture(scope="module")
def config_file():
    return os.path.join(FIXTURE_DIR, 'mapproxy.yaml')


class TestSeedWMTS(SysTest):

    def setup_method(self):
        self.mapproxy_config_file = os.path.join(FIXTURE_DIR, 'mapproxy.yaml')
        self.seed_config_file = os.path.join(FIXTURE_DIR, 'seed.yaml')
        self.mapproxy_config = load_configuration(self.mapproxy_config_file, seed=True)

    def test_seed_wmts_source(self, tmpdir):
        with MockWMTSServ() as serv:
            serv.expects_tile('wmts_layer', 'GLOBAL_MERCATOR', '0', '0', '0').returns(body=Image.new('RGB', (256, 256)).tobytes())

            # Update the WMTS source URL to point to our mock server
            self.mapproxy_config.sources['wmts_source'].conf['url'] = serv.base_url + '/'

            seed_conf = load_seed_tasks_conf(self.seed_config_file, self.mapproxy_config)
            tasks = seed_conf.seeds(['wmts_seed'])

            # Since we implemented WMTSSource, it should no longer raise NotImplementedError
            # Instead it should successfully seed from the WMTS source
            seed(tasks, dry_run=False)

    def test_seed_wmts_source_validates_request_url(self, tmpdir):
        """Test that validates the actual HTTP request URL being generated"""
        import tempfile
        import time
        import http.server
        import socketserver
        import threading
        import urllib.parse
        
        # Create a log file for the HTTP server to write requests
        log_file = os.path.join(str(tmpdir), 'http_requests.log')
        
        class LoggingHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # Log the request to a file
                with open(log_file, 'a') as f:
                    f.write(f"{self.path}\n")
                
                # Return a simple PNG response
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', '0')
                self.end_headers()
                
            def log_message(self, format, *args):
                # Suppress default logging
                pass
        
        # Start a simple HTTP server in a thread
        port = 0  # Let the OS choose a free port
        with socketserver.TCPServer(("localhost", port), LoggingHTTPRequestHandler) as httpd:
            port = httpd.server_address[1]
            server_thread = threading.Thread(target=httpd.serve_forever)
            server_thread.daemon = True
            server_thread.start()
            
            try:
                # Update the WMTS source URL to point to our logging server
                base_url = f'http://localhost:{port}'
                self.mapproxy_config.sources['wmts_source'].conf['url'] = base_url + '/'
                
                seed_conf = load_seed_tasks_conf(self.seed_config_file, self.mapproxy_config)
                tasks = seed_conf.seeds(['wmts_seed'])
                
                # Run seeding
                seed(tasks, dry_run=False)
                
                # Give a moment for the request to be logged
                time.sleep(0.1)
                
                # Read the logged requests
                if os.path.exists(log_file):
                    with open(log_file, 'r') as f:
                        requests = f.read().strip().split('\n')
                    
                    # Verify that the expected URL was requested
                    expected_path = '/wmts_layer/GLOBAL_MERCATOR/0/0/0.png'
                    assert any(expected_path in req for req in requests), f"Expected {expected_path} in requests: {requests}"
                else:
                    pytest.fail("No HTTP requests were logged")
                    
            finally:
                httpd.shutdown()

    def test_seed_wmts_source_with_dimensions(self, tmpdir):
        """Test seeding a WMTS source with dimensions (e.g., time parameter)"""
        with MockWMTSServ() as serv:
            # Expect a tile request with time dimension
            expected_path = '/wmts_layer/GLOBAL_MERCATOR/0/0/0.png?time=2023-01-01'
            serv.expects(expected_path).returns(body=Image.new('RGB', (256, 256)).tobytes())

            # Update the WMTS source URL to point to our mock server  
            self.mapproxy_config.sources['wmts_source'].conf['url'] = serv.base_url + '/'
            
            # Add dimensions to the WMTS source configuration
            self.mapproxy_config.sources['wmts_source'].conf['dimensions'] = {'time': '2023-01-01'}

            seed_conf = load_seed_tasks_conf(self.seed_config_file, self.mapproxy_config)
            tasks = seed_conf.seeds(['wmts_seed'])

            # This should work with dimensions included in the request
            seed(tasks, dry_run=False)

    def test_dimension_parsing_in_seed_config(self, tmpdir):
        """Test that dimensions specified in seed.yaml are correctly parsed"""
        seed_config_with_dims = os.path.join(FIXTURE_DIR, 'seed_with_dimensions.yaml')
        
        seed_conf = load_seed_tasks_conf(seed_config_with_dims, self.mapproxy_config)
        
        # Check that the seed configuration correctly parsed dimensions
        seed_config = seed_conf.conf['seeds']['wmts_seed_with_dims']
        assert 'dimensions' in seed_config
        assert seed_config['dimensions']['time'] == ['2023-01-01', '2023-01-02']
        assert seed_config['dimensions']['elevation'] == ['100']
        
        # Check that dimension combinations are generated correctly
        task_config = seed_conf.seeds(['wmts_seed_with_dims'])[0]
        dimensions = task_config.dimensions
        
        # Should have 2 dimension combinations: 
        # - time=2023-01-01, elevation=100
        # - time=2023-01-02, elevation=100
        expected_combinations = [
            {'time': '2023-01-01', 'elevation': '100'},
            {'time': '2023-01-02', 'elevation': '100'}
        ]
        
        # Get all tasks and their dimensions
        all_tasks = list(seed_conf.seeds(['wmts_seed_with_dims']))
        task_dimensions = [task.dimensions for task in all_tasks]
        
        # Verify we have the expected number of tasks (2 dimension combinations)
        assert len(task_dimensions) == 2
        assert task_dimensions[0] in expected_combinations
        assert task_dimensions[1] in expected_combinations

    def test_wmts_url_generation_validation(self, tmpdir):
        """Test that WMTS URLs are generated correctly with and without dimensions"""
        from mapproxy.source.wmts import WMTSSource
        
        # Test basic URL generation without dimensions
        source = WMTSSource(
            url='http://example.com/wmts/',
            layer='test_layer',
            tilematrixset='GLOBAL_MERCATOR',
            format='png'
        )
        
        # Check the URL template
        expected_template = 'http://example.com/wmts/test_layer/GLOBAL_MERCATOR/%(z)s/%(x)s/%(y)s.png'
        actual_template = source._build_url_template()
        assert actual_template == expected_template
        
        # Test URL generation with dimensions
        source_with_dims = WMTSSource(
            url='http://example.com/wmts/',
            layer='test_layer',
            tilematrixset='GLOBAL_MERCATOR',
            format='png',
            dimensions={'time': '2023-01-01', 'elevation': '100'}
        )
        
        # Check the URL template includes dimensions
        expected_template_with_dims = 'http://example.com/wmts/test_layer/GLOBAL_MERCATOR/%(z)s/%(x)s/%(y)s.png?time=2023-01-01&elevation=100'
        actual_template_with_dims = source_with_dims._build_url_template()
        assert actual_template_with_dims == expected_template_with_dims
        
        # Test dynamic dimensions (as would be used during seeding)
        dynamic_dimensions = {'time': '2023-02-01', 'elevation': '200'}
        dynamic_template = source._build_url_template(dynamic_dimensions)
        expected_dynamic_template = 'http://example.com/wmts/test_layer/GLOBAL_MERCATOR/%(z)s/%(x)s/%(y)s.png?time=2023-02-01&elevation=200'
        assert dynamic_template == expected_dynamic_template

    def test_wmts_tile_url_substitution(self, tmpdir):
        """Test that tile coordinates are correctly substituted in WMTS URLs"""
        from mapproxy.source.wmts import WMTSSource
        from mapproxy.client.tile import TileURLTemplate
        
        # Test with dimensions
        source = WMTSSource(
            url='http://example.com/wmts/',
            layer='test_layer', 
            tilematrixset='GLOBAL_MERCATOR',
            format='png',
            dimensions={'time': '2023-01-01'}
        )
        
        # Build the URL template and test substitution
        template_string = source._build_url_template()
        url_template = TileURLTemplate(template_string, format='png')
        
        # Test URL substitution for a specific tile
        tile_coord = (0, 0, 0)  # x, y, z
        url = url_template.substitute(tile_coord, 'png', source.grid)
        expected_url = 'http://example.com/wmts/test_layer/GLOBAL_MERCATOR/0/0/0.png?time=2023-01-01'
        assert url == expected_url
        
        # Test URL substitution for another tile
        tile_coord = (1, 2, 3)
        url = url_template.substitute(tile_coord, 'png', source.grid)
        expected_url = 'http://example.com/wmts/test_layer/GLOBAL_MERCATOR/3/1/2.png?time=2023-01-01'
        assert url == expected_url