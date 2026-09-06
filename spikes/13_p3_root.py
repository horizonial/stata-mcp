"""P3 根因：为什么 _get_e_cmd 空 + Mata 不能 capture 包裹。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import PystataBackend

def main():
    b = PystataBackend()
    b.init()
    b.execute("sysuse auto, clear")

    # 直接 stata.run regress（不走 _wrap_capture），看 e(cmd) 是否在
    b._stata.run('regress mpg weight price displacement')
    from sfi import Macro
    print("[直接 run regress 后] e(cmd) =", repr(Macro.getGlobal("e(cmd)")))

    # 现在走 execute（带 capture noisily 包裹），看 e(cmd) 是否还在
    b.execute('display "after execute"')
    print("[execute display 后] e(cmd) =", repr(Macro.getGlobal("e(cmd)")))

    # 关键：capture noisily {regress...} 是否清掉 e()？
    b.execute('regress mpg weight')
    print("[execute regress 后] e(cmd) =", repr(Macro.getGlobal("e(cmd)")))

    # Mata 是否必须直接 run，不能 capture 包裹
    MATA = 'mata: st_matrix("_t", (1,2))\nend'
    try:
        r = b.execute(MATA)  # 会被 capture noisily { } 包裹
        print("[execute Mata] rc =", r.rc, "text=", repr(r.text[:120]))
    except Exception as e:
        print("[execute Mata] EXC:", e)

if __name__ == "__main__":
    main()
