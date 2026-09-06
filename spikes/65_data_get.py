import sys, inspect
sys.path.insert(0, "src")
from stata_mcp.stata.pystata_backend import get_backend
b = get_backend()
b.execute("sysuse auto, clear")
from sfi import Data
print("get 签名:", inspect.signature(Data.get))
print("getAt 签名:", inspect.signature(Data.getAt))
print("get 标量:", Data.get(1, 0))        # price obs0
print("get 列表:", Data.get([1,2], [0,1]))
print("getAt:", Data.getAt(1, 0))
