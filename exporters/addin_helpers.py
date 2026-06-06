"""Helper module for CAD addins to download files via API endpoints.

This module provides a unified interface for any CAD addin (FreeCAD, Fusion 360,
SolidWorks, Onshape, etc.) to:
1. Generate STEP/DXF/STL files directly via REST API
2. Track machine_id for trial download limits
3. Handle download errors gracefully

Usage:
    from exporters.addin_helpers import AddinDownloader

    downloader = AddinDownloader('https://cheapcadtools.com', 'my-machine-id')

    params = {
        'family': 'HTD',
        'pitch': '5M',
        'teeth': '20',
        'bore': '8',
        # ... all pulley design params
    }

    try:
        step_data = downloader.download_step(params)
        with open('pulley.step', 'wb') as f:
            f.write(step_data)
    except DownloadLimitExceeded as e:
        print(f"Download limit: {e.count}/{e.limit} per week")
    except DownloadError as e:
        print(f"Download failed: {e}")
"""

import json
import urllib.request
import urllib.error


class DownloadError(Exception):
    """Generic download failure."""
    pass


class DownloadLimitExceeded(DownloadError):
    """Trial download limit reached (2/week)."""
    def __init__(self, count, limit):
        self.count = count
        self.limit = limit
        super().__init__(f'Download limit reached: {count}/{limit} per week')


class AddinDownloader:
    """Simple REST client for downloading via PulleyWebApp API endpoints.

    Works for any CAD addin (FreeCAD, Fusion 360, SolidWorks, Onshape, etc.)
    """

    def __init__(self, base_url, machine_id, timeout=30):
        """Initialize downloader.

        Args:
            base_url: Server URL (e.g., 'https://cheapcadtools.com')
            machine_id: Unique machine identifier for trial tracking
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.machine_id = machine_id
        self.timeout = timeout

    def download_step(self, params):
        """Download STEP file.

        Args:
            params: Dict of pulley design parameters

        Returns:
            Binary STEP file data

        Raises:
            DownloadLimitExceeded: Trial limit reached
            DownloadError: Download failed
        """
        return self._download('step', params)

    def download_dxf(self, params):
        """Download DXF file.

        Args:
            params: Dict of pulley design parameters

        Returns:
            Binary DXF file data

        Raises:
            DownloadLimitExceeded: Trial limit reached
            DownloadError: Download failed
        """
        return self._download('dxf', params)

    def download_stl(self, params):
        """Download STL file.

        Args:
            params: Dict of pulley design parameters

        Returns:
            Binary STL file data

        Raises:
            DownloadLimitExceeded: Trial limit reached
            DownloadError: Download failed
        """
        return self._download('stl', params)

    def _download(self, fmt, params):
        """Internal: POST to API endpoint and return file data."""
        url = f'{self.base_url}/api/download/{fmt}'

        payload = json.dumps({
            'machine_id': self.machine_id,
            'params': params
        }).encode('utf-8')

        req = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()

                # Check if response is JSON error
                if resp.headers.get('Content-Type', '').startswith('application/json'):
                    try:
                        error_data = json.loads(data.decode('utf-8'))
                        if error_data.get('code') == 'DOWNLOAD_LIMIT_EXCEEDED':
                            raise DownloadLimitExceeded(
                                error_data.get('count', 0),
                                error_data.get('limit', 0)
                            )
                        raise DownloadError(error_data.get('error', 'Unknown error'))
                    except (json.JSONDecodeError, KeyError):
                        pass

                return data

        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    error_data = json.loads(e.read().decode('utf-8'))
                    raise DownloadLimitExceeded(
                        error_data.get('count', 0),
                        error_data.get('limit', 0)
                    )
                except (json.JSONDecodeError, KeyError):
                    raise DownloadLimitExceeded(0, 2)
            raise DownloadError(f'HTTP {e.code}: {e.reason}')

        except (urllib.error.URLError, TimeoutError) as e:
            raise DownloadError(f'Connection failed: {e}')

        except Exception as e:
            raise DownloadError(f'Download failed: {e}')


# Example usage for FreeCAD addin:
if __name__ == '__main__':
    # FreeCAD example:
    # from cct_pulley.paths import machine_id
    # downloader = AddinDownloader('https://cheapcadtools.com', machine_id())
    # params = get_design_params_from_user()
    # step_data = downloader.download_step(params)
    # with open(f'{WATCH_DIR}/pulley.step', 'wb') as f:
    #     f.write(step_data)

    print('AddinDownloader helper module loaded.')
    print('Use: from exporters.addin_helpers import AddinDownloader')
