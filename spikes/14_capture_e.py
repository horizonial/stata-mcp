"""P3 根因：capture noisily 包裹是否清掉 e()。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend

def main():
    b = PystataBackend()
    b.init()
    b.execute("sysuse auto, clear")

    from sfi import Macro

    # 场景1：直接 run regress
    b._stata.run("regress mpg weight")
    print("[1] 直接 run regress 后 e(cmd) =", repr(Macro.getGlobal("e(cmd)")))

    # 场景2：capture noisily regress（单行，无 {}）
    b._stata.run("capture noisily regress mpg price")
    print("[2] capture noisily regress 后 e(cmd) =", repr(Macro.getGlobal("e(cmd)")))

    # 场景3：execute 走完整流程（含读 rc 那步）
    r = b.execute("regress mpg displacement")
    print("[3] execute regress 后 e(cmd) =", repr(Macro.getGlobal("e(cmd)")))
    print("    execute rc =", r.rc)

    # 场景4：单独看 execute 里读 rc 的语句是否清 e()
    b._stata.run("capture noisily regress mpg weight")
    b._stata.run("capture noisily scalar _stata_mcp_rc = _rc")
    print("[4] 读 rc 语句后 e(cmd) =", repr(Macro.getGlobal("e(cmd)")))

if __name__ == "__main__":
    main()
