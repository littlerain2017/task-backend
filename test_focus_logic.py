import ast
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from focus_logic import FocusRequest, parse_breakdown


class FocusTests(unittest.TestCase):
    def test_valid_request(self):
        self.assertEqual(FocusRequest(code='code', tasks=[' 写作 ']).tasks, ['写作'])

    def test_bad_request(self):
        for tasks in [[], [''], ['x' * 201], ['x'] * 11]:
            with self.assertRaises(ValidationError):
                FocusRequest(code='code', tasks=tasks)

    def test_output_validation(self):
        good = {'task': '写作', 'steps': ['打开文档', '写一句', '补一段']}
        self.assertEqual(parse_breakdown(json.dumps(good), ['写作']), good)
        for bad in [{**good, 'task': '别的'}, {**good, 'steps': ['一步']}, {**good, 'steps': [1, 2, 3]}, {**good, 'steps': ['', '二', '三']}]:
            with self.assertRaises(ValueError):
                parse_breakdown(json.dumps(bad), ['写作'])

    def endpoint(self, identity=None, output=None):
        app = FastAPI()
        login = AsyncMock(return_value=identity or {'openid': 'test'})
        model = AsyncMock(return_value=(output or '{"task":"写作","steps":["打开文档","写一句","补一段"]}', {}))
        namespace = dict(app=app, asyncio=asyncio, httpx=httpx, HTTPException=HTTPException,
                         FocusRequest=FocusRequest, parse_breakdown=parse_breakdown, json=json,
                         LoginRequest=lambda code: code, login=login, moonshot_chat=model,
                         MOONSHOT_REF_MODEL='test', _focus_slots=asyncio.Semaphore(2))
        fn = next(n for n in ast.parse(Path('main.py').read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'focus_breakdown')
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'main.py', 'exec'), namespace)
        return TestClient(app), model

    def test_endpoint_success(self):
        client, model = self.endpoint()
        r = client.post('/focus-breakdown', json={'code': 'test', 'tasks': ['写作']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['steps']), 3)
        self.assertEqual(model.await_count, 1)

    def test_login_rejected_before_model(self):
        client, model = self.endpoint(identity={'error': 'invalid'})
        self.assertEqual(client.post('/focus-breakdown', json={'code': 'bad', 'tasks': ['写作']}).status_code, 401)
        self.assertEqual(model.await_count, 0)

    def test_bad_model_output(self):
        client, _ = self.endpoint(output='not json')
        self.assertEqual(client.post('/focus-breakdown', json={'code': 'test', 'tasks': ['写作']}).status_code, 502)

if __name__ == '__main__':
    unittest.main()
