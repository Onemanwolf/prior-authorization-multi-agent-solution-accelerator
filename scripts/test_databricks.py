import importlib.util
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import json
import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('supply_seed', ROOT / 'databricks/src/seed.py')
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
bootstrap_spec = importlib.util.spec_from_file_location('databricks_bootstrap', ROOT / 'scripts/bootstrap_databricks.py')
bootstrap = importlib.util.module_from_spec(bootstrap_spec)
bootstrap_spec.loader.exec_module(bootstrap)
deploy_spec = importlib.util.spec_from_file_location('supply_deploy', ROOT / 'scripts/deploy_supply_agent.py')
deploy_agent = importlib.util.module_from_spec(deploy_spec)
deploy_spec.loader.exec_module(deploy_agent)
data_bundle = yaml.safe_load((ROOT / 'databricks/databricks.yml').read_text(encoding='utf-8'))
genie_bundle = yaml.safe_load((ROOT / 'databricks/genie/databricks.yml').read_text(encoding='utf-8'))
azd_template = yaml.safe_load((ROOT / 'azure.yaml').read_text(encoding='utf-8-sig'))


class SupplyDataTests(unittest.TestCase):
    def setUp(self):
        self.data = seed.build_data()

    def test_repeatable_and_synthetic(self):
        self.assertEqual(self.data, seed.build_data())
        self.assertEqual({name: len(rows) for name, rows in self.data.items()}, {
            'products': 12, 'prices': 144, 'branches': 2, 'inventory': 24, 'order_lines': 90,
        })
        for table, rows in self.data.items():
            keys = seed.TABLES[table][1]
            self.assertEqual(len(rows), len({tuple(row[key] for key in keys) for row in rows}))
            self.assertTrue(all(row['is_mock'] is True for row in rows))

    def test_price_boundaries_and_expiry(self):
        for product in self.data['products']:
            for tier in ['RETAIL', 'CONTRACTOR', 'NATIONAL']:
                for day in [date(2026, 1, 1), date(2026, 6, 30), date(2026, 7, 1), date(2026, 12, 31)]:
                    for quantity in [1, 99, 100, 10000]:
                        self.assertGreater(seed.select_price(self.data['prices'], product['sku'], tier, quantity, day), 0)
        for quantity, day in [(0, seed.SNAPSHOT), (1, date(2027, 1, 1))]:
            with self.assertRaises(ValueError):
                seed.select_price(self.data['prices'], 'CON-80', 'RETAIL', quantity, day)

    def test_known_quote(self):
        price = seed.select_price(self.data['prices'], 'CON-80', 'CONTRACTOR', 120, seed.SNAPSHOT)
        self.assertEqual(price, Decimal('6.47'))
        self.assertEqual(price * 120, Decimal('776.40'))
        self.assertEqual(price * 120 + self.data['branches'][0]['delivery_fee'], Decimal('841.40'))

    def test_relationships_and_money(self):
        skus = {row['sku'] for row in self.data['products']}
        branches = {row['branch_id'] for row in self.data['branches']}
        for row in self.data['prices'] + self.data['inventory'] + self.data['order_lines']:
            self.assertIn(row['sku'], skus)
        for row in self.data['inventory'] + self.data['order_lines']:
            self.assertIn(row['branch_id'], branches)
        for row in self.data['order_lines']:
            self.assertEqual(row['line_total'], row['quantity'] * row['unit_price'])
            self.assertIsInstance(row['line_total'], Decimal)
        self.assertTrue(all(row['on_hand'] >= row['reserved'] for row in self.data['inventory']))
        self.assertTrue(any(row['on_hand'] == 0 for row in self.data['inventory']))

    def test_catalog_injection_rejected(self):
        with self.assertRaises(ValueError):
            seed.seed(Mock(), 'main; DROP SCHEMA production')

    def test_unowned_table_rejected(self):
        spark = Mock()
        spark.sql.return_value.first.return_value = {'value': 'another-dataset'}
        with self.assertRaisesRegex(ValueError, 'Refusing'):
            seed.seed(spark, 'construction_supply')
        spark.createDataFrame.assert_not_called()


class BundleContractTests(unittest.TestCase):
    def test_bootstrap_refreshes_windows_path_from_registry(self):
        with patch.object(bootstrap.sys, 'platform', 'win32'), \
                patch.object(bootstrap, '_windows_path_entries', return_value=[r'C:\Databricks']), \
                patch.dict(bootstrap.os.environ, {'PATH': r'C:\Existing'}):
            bootstrap._refresh_windows_path()
            self.assertEqual(
                bootstrap.os.environ['PATH'],
                bootstrap.os.pathsep.join([r'C:\Databricks', r'C:\Existing']),
            )

    def test_azd_hooks_gate_the_nested_agent_after_databricks_bootstrap(self):
        self.assertNotIn('supply-pricing-agent', azd_template['services'])
        postprovision = azd_template['hooks']['postprovision']
        postdeploy = azd_template['hooks']['postdeploy']
        for shell in ('windows', 'posix'):
            self.assertIn('scripts/bootstrap_databricks.py', postprovision[shell]['run'])
            self.assertIn('scripts/deploy_supply_agent.py', postdeploy[shell]['run'])
            self.assertIn('SUPPLY_AGENT_DEPLOYED', postdeploy[shell]['run'])

    def test_seed_bundle_is_manual_and_cost_bounded(self):
        warehouse = data_bundle['resources']['sql_warehouses']['supply_warehouse']
        catalog = data_bundle['resources']['catalogs']['supply_catalog']
        self.assertEqual(data_bundle['bundle']['engine'], 'direct')
        self.assertEqual(catalog['name'], '${var.catalog}')
        self.assertEqual(catalog['storage_root'], '${resources.external_locations.supply_storage.url}/catalog')
        location = data_bundle['resources']['external_locations']['supply_storage']
        self.assertEqual(location['credential_name'], '${var.storage_credential_name}')
        self.assertEqual(location['url'], '${var.storage_url}')
        self.assertFalse(location['skip_validation'])
        self.assertFalse(location['enable_file_events'])
        self.assertEqual(warehouse['auto_stop_mins'], 5)
        self.assertEqual(warehouse['min_num_clusters'], 1)
        self.assertEqual(warehouse['max_num_clusters'], 1)
        self.assertFalse(warehouse['lifecycle']['started'])
        self.assertIn('seed_supply', data_bundle['resources']['jobs'])
        self.assertNotIn('job_runs', data_bundle['resources'])

    def test_workspace_does_not_request_custom_initial_catalog(self):
        source = (ROOT / 'infra/modules/databricks.bicep').read_text(encoding='utf-8-sig')
        self.assertNotIn('initialName:', source)
        self.assertNotIn('defaultCatalog:', source)

    def test_genie_bundle_has_valid_curated_demo_context(self):
        genie = genie_bundle['resources']['genie_spaces']['supply_genie']
        definition = json.loads(genie['serialized_space'])
        self.assertEqual(genie_bundle['bundle']['engine'], 'direct')
        self.assertEqual(len(definition['data_sources']['tables']), 5)
        ordered_identifiers = [table['identifier'] for table in definition['data_sources']['tables']]
        self.assertEqual(ordered_identifiers, sorted(ordered_identifiers))
        identifiers = {table['identifier'] for table in definition['data_sources']['tables']}
        self.assertEqual(identifiers, {
            '${var.catalog}.construction_supply_demo.products',
            '${var.catalog}.construction_supply_demo.prices',
            '${var.catalog}.construction_supply_demo.branches',
            '${var.catalog}.construction_supply_demo.inventory',
            '${var.catalog}.construction_supply_demo.order_lines',
        })
        questions = definition['config']['sample_questions']
        self.assertEqual(len({question['id'] for question in questions}), len(questions))
        instructions = ' '.join(definition['instructions']['text_instructions'][0]['content']).lower()
        for rule in ['synthetic', 'quantity', 'valid_from', 'freight', 'snapshot', 'read-only']:
            self.assertIn(rule, instructions)

    def test_bootstrap_skips_when_disabled_or_cli_missing(self):
        values = {'ENABLE_DATABRICKS': 'false'}
        writes = []
        run_cli = Mock()
        self.assertFalse(bootstrap.bootstrap(values.get, lambda key, value: writes.append((key, value)), run_cli, True))
        run_cli.assert_not_called()
        self.assertIn(('DATABRICKS_READY', 'false'), writes)

        values['ENABLE_DATABRICKS'] = 'true'
        values['DATABRICKS_HOST'] = 'https://adb-example.azuredatabricks.net'
        self.assertFalse(bootstrap.bootstrap(values.get, lambda key, value: writes.append((key, value)), run_cli, False))
        run_cli.assert_not_called()

    def test_bootstrap_only_marks_ready_after_all_steps_pass(self):
        values = {'ENABLE_DATABRICKS': 'true', 'DATABRICKS_HOST': 'https://adb-example.azuredatabricks.net'}
        values.update(DATABRICKS_ACCESS_CONNECTOR_ID='/subscriptions/demo/providers/Microsoft.Databricks/accessConnectors/demo',
                      DATABRICKS_STORAGE_URL='abfss://unity-catalog@demo.dfs.core.windows.net/construction-supply')
        writes = []
        commands = []

        def fake_cli(args, cwd, environment):
            commands.append((args, cwd, environment['DATABRICKS_HOST']))
            if args[:2] == ['api', 'get'] and args[2] == '/api/2.0/preview/scim/v2/Me':
                return json.dumps({'id': 'user-id'})
            if args[:2] == ['api', 'get'] and 'storage-credentials' in args[2]:
                return json.dumps({'storage_credentials': []})
            if args[:2] == ['api', 'post']:
                return args[args.index('--json') + 1]
            if args[:2] == ['bundle', 'deploy']:
                self.assertEqual(environment['BUNDLE_VAR_storage_url'], values['DATABRICKS_STORAGE_URL'])
                self.assertTrue(environment['BUNDLE_VAR_storage_credential_name'].startswith('construction_supply_storage_'))
            if args[:2] == ['warehouses', 'list']:
                return json.dumps({'warehouses': [{'id': 'warehouse-123', 'name': 'construction-supply-demo-dev'}]})
            if args[:2] == ['bundle', 'summary']:
                self.assertEqual(cwd, ROOT / 'databricks/genie')
                self.assertIn('--force-pull', args)
                return json.dumps({'resources': {'genie_spaces': {'supply_genie': {'id': 'a' * 32}}}})
            return ''

        self.assertTrue(bootstrap.bootstrap(values.get, lambda key, value: writes.append((key, value)), fake_cli, True))
        self.assertEqual(writes[-2:], [('DATABRICKS_GENIE_SPACE_ID', 'a' * 32), ('DATABRICKS_READY', 'true')])
        self.assertEqual(sum(args[:2] == ['bundle', 'deploy'] for args, _, _ in commands), 2)
        self.assertTrue(any('warehouse-123' in arg for args, _, _ in commands for arg in args))
        self.assertEqual(commands[0][0][:3], ['api', 'get', '/api/2.0/preview/scim/v2/Me'])
        self.assertFalse(any('/api/2.0/genie/spaces' in args for args, _, _ in commands))

    def test_genie_id_uses_bundle_resource_key_not_title(self):
        summary = {'resources': {'genie_spaces': {
            'supply_genie': {'id': 'a' * 32, 'title': 'Renamed demo'},
            'another_space': {'id': 'b' * 32, 'title': bootstrap.GENIE_TITLE},
        }}}
        self.assertEqual(bootstrap._find_genie_space(json.dumps(summary)), 'a' * 32)

    def test_genie_id_missing_or_invalid_is_rejected(self):
        for space in ({}, {'id': ''}, {'id': 123}, {'id': 'not-an-id'}):
            with self.subTest(space=space), self.assertRaisesRegex(RuntimeError, 'no valid deployed ID'):
                bootstrap._find_genie_space(json.dumps({'resources': {'genie_spaces': {'supply_genie': space}}}))
        with self.assertRaisesRegex(RuntimeError, 'no valid deployed ID'):
            bootstrap._find_genie_space('{}')

    def test_storage_credential_reused_and_conflicts_rejected(self):
        connector = '/subscriptions/demo/providers/Microsoft.Databricks/accessConnectors/demo'
        name = 'construction_supply_storage_' + bootstrap.hashlib.sha256(connector.lower().encode()).hexdigest()[:12]
        credential = {'name': name, 'azure_managed_identity': {'access_connector_id': connector}}
        run = Mock(return_value=json.dumps({'storage_credentials': [credential]}))
        self.assertEqual(bootstrap._ensure_storage_credential(connector, run, ROOT, {}), name)
        run.assert_called_once()
        credential['azure_managed_identity']['access_connector_id'] = 'another-connector'
        run.return_value = json.dumps({'storage_credentials': [credential]})
        with self.assertRaisesRegex(RuntimeError, 'Refusing'):
            bootstrap._ensure_storage_credential(connector, run, ROOT, {})

    def test_storage_credential_pagination_and_validated_creation(self):
        commands = []

        def run(args, *_rest):
            commands.append(args)
            if args[1] == 'post':
                body = json.loads(args[args.index('--json') + 1])
                self.assertFalse(body['skip_validation'])
                self.assertFalse(body['read_only'])
                self.assertEqual(body['azure_managed_identity']['access_connector_id'], 'connector')
                return json.dumps(body)
            return json.dumps({'storage_credentials': [], 'next_page_token': 'next+page'} if len(commands) == 1 else {})

        bootstrap._ensure_storage_credential('connector', run, ROOT, {})
        self.assertIn('page_token=next%2Bpage', commands[1][2])
        self.assertEqual(commands[2][:2], ['api', 'post'])

    def test_missing_storage_outputs_skip_before_bundle_deployment(self):
        values = {'ENABLE_DATABRICKS': 'true', 'DATABRICKS_HOST': 'https://adb-example.azuredatabricks.net'}
        run = Mock(return_value=json.dumps({'id': 'user-id'}))
        writes = Mock()
        self.assertFalse(bootstrap.bootstrap(values.get, writes, run, True))
        run.assert_called_once()
        self.assertNotIn(('DATABRICKS_READY', 'true'), [call.args for call in writes.call_args_list])

    def test_bootstrap_failure_never_marks_ready(self):
        values = {'ENABLE_DATABRICKS': 'true', 'DATABRICKS_HOST': 'https://adb-example.azuredatabricks.net'}
        writes = []

        def fail_cli(*_args):
            raise RuntimeError('simulated workspace unavailable')

        self.assertFalse(bootstrap.bootstrap(values.get, lambda key, value: writes.append((key, value)), fail_cli, True))
        self.assertNotIn(('DATABRICKS_READY', 'true'), writes)

    def test_supply_agent_is_skipped_when_databricks_is_not_ready(self):
        run = Mock(return_value=True)
        set_value = Mock()
        self.assertFalse(deploy_agent.deploy(lambda _key: 'false', set_value, run))
        run.assert_not_called()
        set_value.assert_called_once_with('SUPPLY_AGENT_DEPLOYED', 'false')

    def test_supply_agent_requires_genie_and_deploys_from_nested_project(self):
        values = {
            'DATABRICKS_READY': 'true', 'DATABRICKS_HOST': 'https://adb-example.azuredatabricks.net',
            'DATABRICKS_GENIE_SPACE_ID': 'a' * 32, 'AZURE_ENV_NAME': 'test-env',
            'AZURE_SUBSCRIPTION_ID': 'subscription-id', 'AZURE_LOCATION': 'eastus2',
        }
        calls = []

        def fake_run(arguments, cwd):
            calls.append((arguments, cwd))
            return True

        set_value = Mock()
        self.assertTrue(deploy_agent.deploy(values.get, set_value, fake_run))
        self.assertTrue(all(cwd == deploy_agent.SUPPLY_PROJECT for _, cwd in calls))
        self.assertEqual(calls[-1][0], ['azd', 'deploy', '--environment', 'test-env-supply', '--no-prompt'])
        self.assertEqual(set_value.call_args_list[-1].args, ('SUPPLY_AGENT_DEPLOYED', 'true'))

        values['DATABRICKS_GENIE_SPACE_ID'] = ''
        calls.clear()
        self.assertFalse(deploy_agent.deploy(values.get, set_value, fake_run))
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()