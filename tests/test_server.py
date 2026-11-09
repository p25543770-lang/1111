#!/usr/bin/env python3
"""Integration tests; pass the compiled executable as the first argument."""
import json
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

exe = sys.argv.pop(1) if len(sys.argv) > 1 else './password_demo'


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            cls.port = s.getsockname()[1]
        cls.process = subprocess.Popen([exe, '--port', str(cls.port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=.1):
                    break
            except OSError:
                if cls.process.poll() is not None:
                    raise RuntimeError('Server exited early')
                time.sleep(.02)
        else:
            raise RuntimeError('Server did not start')
        cls.url = f'http://127.0.0.1:{cls.port}'

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stdout.close()
        cls.process.stderr.close()

    def post(self, **params):
        data = urllib.parse.urlencode(params).encode()
        try:
            response = urllib.request.urlopen(self.url + '/api/guess', data=data, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_page(self):
        with urllib.request.urlopen(self.url + '/') as response:
            self.assertIn('Подбор пароля', response.read().decode())

    def test_found_and_order(self):
        status, result = self.post(password='12', alphabet='digits', length=2)
        self.assertEqual(status, 200)
        self.assertTrue(result['found'])
        self.assertEqual(result['password'], '12')
        self.assertEqual(result['attempts'], 23)

    def test_bad_data(self):
        for params in [dict(password='abc', alphabet='digits', length=3),
                       dict(password='', alphabet='digits', length=2),
                       dict(password='123', alphabet='digits', length=2),
                       dict(password='1', alphabet='digits', length=9),
                       dict(password='1', alphabet='unknown', length=2)]:
            status, result = self.post(**params)
            self.assertEqual(status, 400)
            self.assertIn('error', result)

    def test_limit(self):
        status, result = self.post(password='zzzzzzzz', alphabet='lower', length=8)
        self.assertEqual(status, 200)
        self.assertFalse(result['found'])
        self.assertTrue(result['limit_reached'])
        self.assertEqual(result['attempts'], 2_000_000)

    def test_invalid_host_rejected(self):
        process = subprocess.run([exe, '--host', 'not-an-ip'], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('Invalid host', process.stderr)

    def test_public_and_custom_host(self):
        for args in [['--public'], ['--host', '127.0.0.1']]:
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                port = s.getsockname()[1]
            process = subprocess.Popen([exe, *args, '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertIn('Listening on', process.stdout.readline().decode())
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
                    self.assertEqual(response.status, 200)
            finally:
                process.terminate()
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_not_found_route(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(self.url + '/nope')
        self.assertEqual(exc.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
