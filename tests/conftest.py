import sys
from pathlib import Path

# scripts/ dung import phang (khong package) - them vao sys.path de test import truc tiep
# duoc cac module gsheet_* (vd `import gsheet_config`) giong cach affiliate_scrape_server.py
# import chung.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
