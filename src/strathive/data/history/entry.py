"""Load only StratHive's worker package into the isolated component interpreter."""
import importlib.util
from pathlib import Path
import runpy
import sys

package = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('strathive', package / '__init__.py',
                                            submodule_search_locations=[str(package)])
module = importlib.util.module_from_spec(spec)
sys.modules['strathive'] = module
spec.loader.exec_module(module)
runpy.run_module('strathive.data.history.worker', run_name='__main__', alter_sys=True)
