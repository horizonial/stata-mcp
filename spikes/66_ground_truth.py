import sys
sys.path.insert(0, "src")
from stata_mcp.session import SessionManager


def main():
    mgr = SessionManager(max_sessions=1)
    try:
        s = mgr.get_or_create("default")
        s.execute("sysuse auto, clear")
        r = s.execute("regress mpg weight")
        st = r.structured
        print("regress coefs:", [(c["var"], round(c["coef"], 9), round(c["se"], 10), round(c["t"], 6)) for c in st["coefs"]])
        print("r2:", st["r2"], "N:", st["N"])
        r = s.execute("logit foreign weight")
        st = r.structured
        print("logit coefs:", [(c["var"], round(c["coef"], 9)) for c in st["coefs"]])
    finally:
        mgr.close_all()


if __name__ == "__main__":
    main()
