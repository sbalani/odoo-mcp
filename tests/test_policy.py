"""Fast policy checks independent of an Odoo installation."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('policy', Path(__file__).parents[1] / 'odoo_mcp/policy.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PolicyTests(unittest.TestCase):
    def test_block_unexposed_models_fields_and_traversals(self):
        for args in [
            {'resource': 'ir.config_parameter'},
            {'resource': 'users', 'fields': ['password']},
            {'resource': 'sales', 'filters': [['partner_id.bank_ids', '=', 1]]},
            {'resource': 'crm', 'filters': ['|']},
            {'resource': 'crm', 'filters': [['name', 'any', []]]},
            {'resource': 'crm', 'limit': 1000},
            {'resource': 'crm', 'limit': True},
            {'resource': 'crm', 'offset': -1},
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                p.search_args(args)

    def test_write_allowlist(self):
        for values in [{'company_id': 1}, {'order_ids': []}, {'partner_id': [0, 0, {}]},
                       {'probability': 101}, {'expected_revenue': float('nan')}, {'name': ' '}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                p.crm_values(values)
        self.assertEqual(p.crm_values({'name': 'Valid'}, True), {'name': 'Valid'})

    def test_no_write_tools_for_protected_resources(self):
        tools = p.READ_TOOLS + p.CRM_TOOLS + p.CHAT_TOOLS + p.TODO_TOOLS
        mutations = [t['name'] for t in tools if not t['annotations']['readOnlyHint']]
        self.assertTrue(all(n.startswith(('crm_', 'chat_', 'todo_')) for n in mutations))
        self.assertTrue(all(t['inputSchema']['additionalProperties'] is False for t in tools))

    def test_todo_values_cannot_escape_personal_scope(self):
        for values in [{'project_id': 1}, {'user_ids': [1]}, {'parent_id': 1},
                       {'company_id': 1}, {'state': '1_done'}, {'priority': '3'},
                       {'date_deadline': 'tomorrow'}, {'description': False}, {'name': ' '}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                p.todo_values(values)
        self.assertEqual(p.todo_values({'name': 'Plan', 'date_deadline': False}, True),
                         {'name': 'Plan', 'date_deadline': False})
        p.todo_values({'date_deadline': '2030-01-01 12:30:00'})

    def test_default_search_is_bounded(self):
        self.assertEqual(p.search_args({'resource': 'inventory'})[-2:], (50, 0))

    def test_no_unknown_arguments(self):
        with self.assertRaises(ValueError):
            p.validate_arguments(p.READ_TOOLS[0], {'resource': 'crm', 'context': {'sudo': True}})


if __name__ == '__main__':
    unittest.main()
