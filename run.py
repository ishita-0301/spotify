"""Entry point — start the Workload Management System web app.

    python run.py

Then open http://127.0.0.1:5000
"""
from wms.web.app import create_app

app = create_app()

if __name__ == "__main__":
    # use_reloader=False so the background engine threads aren't started twice.
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
