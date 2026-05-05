#!/usr/bin/env python3
"""Desenvolvimento local — python run.py"""
import sys, os, subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 7821

def check_deps():
    faltando = []
    for pkg in ["fastapi","uvicorn","sqlalchemy","httpx","jose","passlib"]:
        try: __import__(pkg)
        except ImportError: faltando.append(pkg)
    if faltando:
        print(f"ERRO: pip install -r requirements.txt")
        print(f"Faltando: {faltando}")
        return False
    return True

if __name__ == "__main__":
    if not check_deps(): sys.exit(1)

    if "--test" in sys.argv:
        subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v",
                        "--tb=short", "--no-header", "-p", "no:warnings", "--no-cov"],
                       cwd=BASE_DIR)
        sys.exit(0)

    if "--reset" in sys.argv:
        db = os.path.join(BASE_DIR, "erp.db")
        if os.path.exists(db): os.remove(db)
        sys.path.insert(0, BASE_DIR)
        from backend.database import init_db
        init_db()
        print("✅ Banco recriado"); sys.exit(0)

    sys.path.insert(0, BASE_DIR)
    from backend.database import init_db
    init_db()

    print(f"\n{'='*58}")
    print(f"  ERP MatCon — Desenvolvimento Local")
    print(f"{'='*58}")
    print(f"  Sistema: http://localhost:{PORT}/frontend/index.html")
    print(f"  API:     http://localhost:{PORT}/docs")
    print(f"  Login:   admin@matcon.com.br / admin123")
    print(f"{'='*58}\n")

    import uvicorn
    uvicorn.run("backend:app", host="0.0.0.0", port=PORT,
                reload=False, log_level="info")
