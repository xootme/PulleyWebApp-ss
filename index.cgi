#!/opt/alt/python311/bin/python3
import sys
import os
import traceback

# Force the SCRIPT_NAME environment variable so Flask handles the subfolder routing
os.environ['SCRIPT_NAME'] = '/tst_pulleys'

try:
    # Activate virtual environment paths
    app_dir = '/home/xootpro/public_html/cheapcadtools/tst_pulleys'
    sys.path.insert(0, f'{app_dir}/venv/lib/python3.11/site-packages')
    sys.path.insert(0, f'{app_dir}/venv/lib64/python3.11/site-packages')
    sys.path.insert(0, app_dir)

    from wsgiref.handlers import CGIHandler
    from app import app

    CGIHandler().run(app)
except Exception as e:
    # Catch setup errors and print them to the browser for easier debugging
    print("Content-Type: text/plain\n")
    print(traceback.format_exc())
