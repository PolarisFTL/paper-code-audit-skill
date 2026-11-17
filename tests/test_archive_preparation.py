"""Run the same safe-extraction tests shipped beside the helper."""
import importlib.util
from pathlib import Path
import sys

SCRIPTS=Path(__file__).resolve().parents[1]/'skills'/'paper-code-audit'/'scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('archive_preparation_contract_tests',SCRIPTS/'test_prepare_inputs.py')
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
for name in dir(module):
    value=getattr(module,name)
    if isinstance(value,type) and name.endswith('Tests'):globals()[name]=value
