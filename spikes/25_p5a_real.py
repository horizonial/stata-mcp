"""P5a 实测：三个数据工具 + 真实 dta + 中文路径 + 审计拒绝。"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stata_mcp.stata.pystata_backend import get_backend
from stata_mcp.tools.load_data import stata_load_data
from stata_mcp.tools.inspect_data import stata_inspect_data
from stata_mcp.tools.get_results import stata_get_results

# 本 spike 需在"含真实 dta 数据的目录"下运行（load_data 的路径审计允许 cwd）。
# 用法示例：python spikes/25_p5a_real.py  C:\\path\\to\\your_data_dir
DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

def main():
    # cwd 设为数据目录，使 load_data 的默认审计允许读这里的文件
    os.chdir(DATA_DIR)

    b = get_backend()

    # 1) load_data 英文 dta
    r = stata_load_data({"source": "new.dta"}, None)
    print("=== [1] load new.dta ===")
    print("rc:", r.rc, "structured:", json.dumps(r.structured, ensure_ascii=False))

    # 2) inspect_data describe
    r = stata_inspect_data({"action": "describe"}, None)
    print("\n=== [2] inspect describe ===")
    print("rc:", r.rc, "structured:", json.dumps(r.structured, ensure_ascii=False)[:300])

    # 3) inspect_data summarize（单变量）
    r = stata_inspect_data({"action": "summarize", "variables": ["_N"]}, None)
    print("\n=== [3] inspect summarize (_N) ===")
    print("rc:", r.rc)

    # 4) 审计拒绝：cwd 之外的文件
    r = stata_load_data({"source": r"C:\Windows\System32\notepad.exe"}, None)
    print("\n=== [4] load 越界文件（应拒绝） ===")
    print("rc:", r.rc, "text:", r.text[:80])

    # 5) 中文路径 load
    r = stata_load_data({"source": "分析数据.dta", "clear": True}, None)
    print("\n=== [5] load 分析数据.dta（中文） ===")
    print("rc:", r.rc, "structured:", json.dumps(r.structured, ensure_ascii=False))

    # 6) get_results：先 regress 再 summarize，看存疑点4
    b.execute("sysuse auto, clear")
    b.execute("regress mpg weight")
    b.execute("summarize mpg")
    r = stata_get_results({}, None)
    print("\n=== [6] get_results（先 regress 后 summarize） ===")
    print("rc:", r.rc)
    print("structured:", json.dumps(r.structured, ensure_ascii=False)[:400])

    print("\nDONE")

if __name__ == "__main__":
    main()
