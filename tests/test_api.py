import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from hermes_omnigent.runtime import Failure


class APITests(unittest.TestCase):
    def setUp(self):
        from hermes_omnigent import runtime
        self.active = True
        self.jobs = []
        def start(action, **inputs):
            self.jobs.append((action, inputs))
            return {'id': 'own-job', 'running': True}
        self.fake = types.SimpleNamespace(activate=lambda **kwargs: None, active=lambda: self.active,
             start=start, status=lambda: {'phase': 'needs-login', 'pairingRequired': True}, reset_pairing=lambda: None)
        def require(request):
            if request.headers.get('x-hermes-token') != 'test-header':
                raise HTTPException(401, detail='Unauthorized')
        modules = {'hermes_cli': types.ModuleType('hermes_cli'),
                   'hermes_cli.web_server': types.SimpleNamespace(_require_token=require)}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('hermes_api_test', Path(__file__).resolve().parents[1] / 'dashboard/api.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.object(runtime, 'get_runtime', return_value=self.fake):
            spec.loader.exec_module(self.module)
        app = FastAPI()
        app.include_router(self.module.router, prefix='/api/plugins/hermes-omnigent')
        self.client = TestClient(app)
        self.url = '/api/plugins/hermes-omnigent'
        self.headers = {'x-hermes-token': 'test-header'}

    def test_pair_requires_host_auth_and_enabled_plugin(self):
        values = {'server': 'https://server.example', 'username': 'user', 'password': 'secret'}
        self.assertEqual(self.client.post(self.url + '/pair', json=values).status_code, 401)
        self.assertFalse(self.jobs)
        self.active = False
        self.assertEqual(self.client.post(self.url + '/pair', json=values, headers=self.headers).status_code, 403)
        self.assertFalse(self.jobs)

    def test_invalid_pair_never_echoes_secret_even_on_extra_fields(self):
        values = {'server': [], 'username': 'user', 'password': 'never-echo-secret', 'extra': 'never-echo-secret'}
        response = self.client.post(self.url + '/pair', json=values, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('never-echo-secret', response.text)
        self.assertNotIn('input', response.text)

    def test_malformed_pair_is_rejected_before_revoking_a_working_connection(self):
        for server, username, password in [('not-a-url', 'user', 'secret'),
                ('https://server.example', '   ', 'secret'), ('https://server.example', 'user', '')]:
            with self.subTest(server=server, username=username):
                response = self.client.post(self.url + '/pair', json={
                    'server': server, 'username': username, 'password': password}, headers=self.headers)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(self.jobs)
                self.assertNotIn('secret', response.text)

    def test_pair_response_contains_no_password_and_delivers_directly_to_job(self):
        values = {'server': 'https://server.example', 'username': 'user', 'password': 'ephemeral-secret'}
        response = self.client.post(self.url + '/pair', json=values, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('ephemeral-secret', response.text)
        self.assertEqual(self.jobs, [('pair', {**values, 'allowDirectHermes': False})])

    def test_busy_returns_safe_code_without_raw_exception(self):
        def fail(*args, **kwargs):
            raise Failure('OPERATION_BUSY')
        self.fake.start = fail
        response = self.client.post(self.url + '/verify', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], 'OPERATION_BUSY')

    def test_execution_opt_in_cannot_be_coerced_from_a_string(self):
        values = {'server': 'https://server.example', 'username': 'user', 'password': 'secret', 'allowDirectHermes': 'true'}
        response = self.client.post(self.url + '/pair', json=values, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.jobs)


if __name__ == '__main__':
    unittest.main()
