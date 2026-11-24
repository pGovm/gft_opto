"""Shared diagnostics remain connected to real validation and calculation paths."""
import ast
from pathlib import Path
from string import Formatter
import unittest
from unittest.mock import patch

from gft_opto.gui import messages
from gft_opto.logic.protection_study import evaluate_zones
from gft_opto.logic.momentary_calc2 import Load, Scenario


class SharedMessages(unittest.TestCase):
    def test_validation_uses_catalog_at_runtime(self):
        with patch.object(messages,'PROTECTION_STUDY_NETLIST_MUST_BE_AN_OBJECT','Changed validation text'):
            with self.assertRaisesRegex(ValueError,'Changed validation text'):
                evaluate_zones(None,[])

    def test_calculator_warning_keeps_precision_and_load_name(self):
        s=Scenario('test')
        s.add_load(Load('Trip coil',1234.56))
        s.add_load(Load('Empty coil',0))
        self.assertEqual(s.calculate()['warnings'],[
            "Peak current is 1234.6 A — that's over 1000 A, please double-check your inputs.",
            "'Empty coil' has zero or negative amps — please check."])

    def test_all_catalog_references_and_format_arguments_are_valid(self):
        package=Path(messages.__file__).parents[1]
        for path in package.rglob('*.py'):
            if path.name=='messages.py':continue
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id=='messages':
                    self.assertIsInstance(getattr(messages,node.attr),str,(path,node.lineno))
                if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                        and node.func.attr=='format' and isinstance(node.func.value,ast.Attribute)
                        and isinstance(node.func.value.value,ast.Name) and node.func.value.value.id=='messages'):
                    continue
                text=getattr(messages,node.func.value.attr)
                fields=[field for _,field,_,_ in Formatter().parse(text) if field is not None]
                self.assertTrue(all(field.isdigit() and int(field)<len(node.args) for field in fields),
                                (path,node.lineno,text))
