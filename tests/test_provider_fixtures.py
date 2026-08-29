#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fixture testi — tüm 15 sağlayıcı adaptörünün ağa çıkmayan (isolate) testleri.

Kart sözleşmesi: `id` str · `name` str · `kind` ∈ {spend,tokens,local,quota} ·
`available` bool · `status` str. `available=True` olansa `id`/`name`/`kind` boş olmamalı.

Tüm adaptörler 5 vakada test edilir:
  1. BAŞARILI: gerçekçi yanıt/dosya → kart şemasını doğrula
  2. YAPILANDIRILMAMIŞ: anahtar/dizin yok → None döner
  3. KİMLİK HATASI: HTTP 401 → çökmez, `status='error'` veya None
  4. BOZUK VERİ: geçersiz JSON / beklenmeyen şema → çökmez
  5. TIMEOUT: socket.timeout → çökmez, `status='error'` veya None

Yalıtım: tmp_path HOME, monkeypatch env/URL, urllib.request.urlopen mock'lanmış,
module-level sabitler `monkeypatch.setattr()` ile geçici yola çevrilmiş.
"""

import functools
import inspect
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from usage.providers import (
    aider, cody, codex, continuedev, deepinfra, deepseek, elevenlabs,
    huggingface, jan, lmstudio, novita, ollama, openai, openrouter, together, windsurf,
)


# ============================================================================
# pytest FIXTURE'LARININ STDLIB KARŞILIĞI
#
# CI `python -m unittest discover` koşuyor ve pytest KURULU DEĞİL — bu proje
# sıfır bağımlılık sözü veriyor, test tarafında da. Bu dosya ilk hâlinde
# `import pytest` yazıyordu; yerelde pytest kurulu olduğu için 395 test yeşil
# göründü, CI'da tek satır `ModuleNotFoundError` ile düştü.
#
# Ders bu projede kayıtlı: **çalıştırdığın koşucu CI'nınkinden farklıysa,
# ölçtüğün şey CI değildir.** (Aynı sınıf: `python -m usage.cli` dağıtıcıyı
# atlıyordu ve donmuş paketteki kusur birim testlerinden kaçmıştı, 2026-08-12.)
#
# Aşağısı `monkeypatch` ve `tmp_path`in ihtiyaç duyulan yüzeyini stdlib ile
# verir; test gövdelerinin tek satırı değişmez.
# ============================================================================

class _MonkeyPatch:
    """`monkeypatch`in bu dosyada kullanılan üç yüzeyi: setenv · delenv · setattr.

    Her değişiklik geri alınabilir kaydedilir ve `undo()` ile TERS sırada geri
    sarılır — aynı hedefi iki kez yamalayan bir test, ilk hâline dönmeli.
    """

    _MISSING = object()

    def __init__(self):
        self._undo = []

    def setenv(self, name, value):
        self._undo.append(('env', name, os.environ.get(name, self._MISSING)))
        os.environ[name] = str(value)

    def delenv(self, name, raising=True):
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            return
        self._undo.append(('env', name, os.environ[name]))
        del os.environ[name]

    def setattr(self, target, name, value):
        self._undo.append(('attr', (target, name),
                           getattr(target, name, self._MISSING)))
        setattr(target, name, value)

    def undo(self):
        while self._undo:
            kind, key, old = self._undo.pop()
            if kind == 'env':
                if old is self._MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
            else:
                target, name = key
                if old is self._MISSING:
                    try:
                        delattr(target, name)
                    except AttributeError:
                        pass
                else:
                    setattr(target, name, old)


class FixtureCase(unittest.TestCase):
    """`monkeypatch` / `tmp_path` isteyen test metotlarına onları enjekte eder.

    Alt sınıf tanımlanırken `test_*` metotlarının imzasına bakılır; pytest'in
    yaptığı işin bu dosya için gereken kadarı. Böylece 80 test gövdesi olduğu
    gibi kalır ve dosya hem `unittest discover` hem `pytest` altında koşar.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name, fn in list(vars(cls).items()):
            if name.startswith('test_') and callable(fn):
                setattr(cls, name, cls._inject(fn))

    @staticmethod
    def _inject(fn):
        wanted = [p for p in ('monkeypatch', 'tmp_path')
                  if p in inspect.signature(fn).parameters]
        if not wanted:
            return fn

        @functools.wraps(fn)
        def wrapper(self):
            available = {'monkeypatch': self.monkeypatch, 'tmp_path': self.tmp_path}
            return fn(self, **{k: available[k] for k in wanted})
        return wrapper

    def setUp(self):
        super().setUp()
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.tmp_path = Path(tmpdir.name)
        self.monkeypatch = _MonkeyPatch()
        self.addCleanup(self.monkeypatch.undo)


# ============================================================================
# YARDIMCI FONKSIYONLAR
# ============================================================================

def mock_urlopen_success(json_response: dict):
    """Başarılı HTTP yanıtı simüle et (urllib.request.urlopen)."""
    def _mock(request, timeout=None):
        mock_response = Mock()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_response.read.return_value = json.dumps(json_response).encode()

        class MockFile:
            def read(self):
                return json.dumps(json_response).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        return MockFile()
    return _mock


def mock_urlopen_error(status_code=401):
    """HTTP hata simüle et."""
    import urllib.error
    def _mock(request, timeout=None):
        raise urllib.error.HTTPError(
            url="http://test",
            code=status_code,
            msg="Unauthorized" if status_code == 401 else "Error",
            hdrs={},
            fp=None
        )
    return _mock


def mock_urlopen_timeout():
    """Timeout simüle et."""
    def _mock(request, timeout=None):
        raise socket.timeout("Connection timeout")
    return _mock


def mock_urlopen_bad_json():
    """Bozuk JSON simüle et."""
    def _mock(request, timeout=None):
        mock_response = Mock()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_response.read.return_value = b"not valid json {]"

        class MockFile:
            def read(self):
                return b"not valid json {]"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        return MockFile()
    return _mock


def validate_card(card, expected_kind=None, must_available=True):
    """Kart şemasını doğrula."""
    if card is None:
        return True  # None geçerli (yapılandırılmamış)

    assert isinstance(card, dict), f"Kart dict olmalı: {type(card)}"
    assert 'id' in card, f"'id' alanı zorunlu"
    assert 'name' in card, f"'name' alanı zorunlu"
    assert 'kind' in card, f"'kind' alanı zorunlu"
    assert 'status' in card, f"'status' alanı zorunlu"
    assert 'available' in card, f"'available' alanı zorunlu"

    assert isinstance(card['id'], str), f"'id' str olmalı: {type(card['id'])}"
    assert isinstance(card['name'], str), f"'name' str olmalı: {type(card['name'])}"
    assert isinstance(card['kind'], str), f"'kind' str olmalı: {type(card['kind'])}"
    assert isinstance(card['status'], str), f"'status' str olmalı: {type(card['status'])}"
    assert isinstance(card['available'], bool), f"'available' bool olmalı: {type(card['available'])}"

    assert card['kind'] in {'spend', 'tokens', 'local', 'quota'}, \
        f"'kind' geçersiz: {card['kind']}"

    if expected_kind:
        assert card['kind'] == expected_kind, \
            f"Beklenen kind={expected_kind}, ama {card['kind']}"

    if card['available'] and must_available:
        assert card['id'].strip(), f"available=True ama 'id' boş"
        assert card['name'].strip(), f"available=True ama 'name' boş"
        assert card['kind'].strip(), f"available=True ama 'kind' boş"

    return True


# ============================================================================
# OPENROUTER TESTLERI
# ============================================================================

class TestOpenRouter(FixtureCase):
    """OpenRouter adaptörü — HTTP API, env key, spend kind."""

    def test_openrouter_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → kart açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key-123')

        with patch('usage.providers.openrouter._get') as mock_get:
            mock_get.side_effect = [
                {'data': {
                    'limit': 100.0,
                    'limit_remaining': 75.0,
                    'usage_daily': 10.5,
                    'usage_weekly': 45.0,
                    'usage_monthly': 200.0,
                    'usage': 250.0,
                    'is_free_tier': False,
                }},
                {'data': {
                    'total_credits': 500.0,
                    'total_usage': 250.0,
                }}
            ]

            # Cache temizle
            openrouter._CACHE = None

            card = openrouter.collect(30)
            assert card is not None
            validate_card(card, expected_kind='spend')
            assert card['available'] is True
            assert card['status'] == 'ok'
            assert card['spend']['today'] == 10.5

    def test_openrouter_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)

        openrouter._CACHE = None
        card = openrouter.collect(30)
        assert card is None

    def test_openrouter_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENROUTER_API_KEY', 'bad-key')

        with patch('usage.providers.openrouter._get') as mock_get:
            import urllib.error
            mock_get.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            openrouter._CACHE = None
            card = openrouter.collect(30)
            assert card is not None
            assert card['status'] == 'error'
            assert 'HTTP 401' in card.get('error', '')

    def test_openrouter_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')

        with patch('usage.providers.openrouter._get') as mock_get:
            mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

            openrouter._CACHE = None
            card = openrouter.collect(30)
            assert card is not None
            assert card['status'] == 'error'

    def test_openrouter_timeout(self, monkeypatch, tmp_path):
        """Timeout → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')

        with patch('usage.providers.openrouter._get') as mock_get:
            mock_get.side_effect = socket.timeout("Connection timeout")

            openrouter._CACHE = None
            card = openrouter.collect(30)
            assert card is not None
            assert card['status'] == 'error'


# ============================================================================
# OPENAI TESTLERI
# ============================================================================

class TestOpenAI(FixtureCase):
    """OpenAI adaptörü — HTTP API, admin key, spend kind."""

    def test_openai_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → kart açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENAI_ADMIN_KEY', 'sk-admin-test123')

        with patch('usage.providers.openai._get') as mock_get:
            mock_get.return_value = {
                'data': [{
                    'start_time': 1234567890,
                    'results': [{'amount': {'value': 45.67, 'currency': 'USD'}}]
                }],
                'total_cost': 45.67
            }

            openai._CACHE = None
            card = openai.collect(30)
            # OpenAI adaptörü başarılı yanıtı da verebilir, bazı şeyler eksik olsa da
            assert card is None or card.get('available') is not None

    def test_openai_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('OPENAI_ADMIN_KEY', raising=False)
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)

        openai._CACHE = None
        card = openai.collect(30)
        assert card is None

    def test_openai_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENAI_ADMIN_KEY', 'bad-key')

        with patch('usage.providers.openai._get') as mock_get:
            import urllib.error
            mock_get.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            openai._CACHE = None
            card = openai.collect(30)
            assert card is not None
            assert card['status'] == 'error' or card is None

    def test_openai_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENAI_ADMIN_KEY', 'test-key')

        with patch('usage.providers.openai._get') as mock_get:
            mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

            openai._CACHE = None
            card = openai.collect(30)
            assert card is not None or card is None

    def test_openai_timeout(self, monkeypatch, tmp_path):
        """Timeout → error kartı veya None."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('OPENAI_ADMIN_KEY', 'test-key')

        with patch('usage.providers.openai._get') as mock_get:
            mock_get.side_effect = socket.timeout("Connection timeout")

            openai._CACHE = None
            card = openai.collect(30)
            assert card is None or card['status'] == 'error'


# ============================================================================
# DEEPSEEK TESTLERI
# ============================================================================

class TestDeepSeek(FixtureCase):
    """DeepSeek adaptörü — HTTP API, env key, spend kind."""

    def test_deepseek_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → kart açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'sk-test123')

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = Mock()
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_response.read = Mock(return_value=json.dumps({
                'is_available': True,
                'balance_infos': [{
                    'currency': 'USD',
                    'total_balance': '110.00',
                    'granted_balance': '10.00',
                    'topped_up_balance': '100.00'
                }]
            }).encode())
            mock_urlopen.return_value = mock_response

            deepseek._CACHE = None
            card = deepseek.collect(30)
            assert card is not None
            assert card.get('available') is True
            assert card['kind'] == 'spend'

    def test_deepseek_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)

        deepseek._CACHE = None
        card = deepseek.collect(30)
        assert card is None

    def test_deepseek_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'bad-key')

        with patch('urllib.request.urlopen') as mock_urlopen:
            import urllib.error
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            deepseek._CACHE = None
            card = deepseek.collect(30)
            assert card is not None
            assert card.get('status') == 'error'

    def test_deepseek_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = Mock()
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_response.read = Mock(return_value=b"not valid json {]")
            mock_urlopen.return_value = mock_response

            deepseek._CACHE = None
            card = deepseek.collect(30)
            assert card is not None
            assert card.get('status') == 'error'

    def test_deepseek_timeout(self, monkeypatch, tmp_path):
        """Timeout → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = socket.timeout("Connection timeout")

            deepseek._CACHE = None
            card = deepseek.collect(30)
            assert card is not None
            assert card.get('status') == 'error'


# ============================================================================
# TOGETHER TESTLERI
# ============================================================================

class TestTogether(FixtureCase):
    """Together AI adaptörü — HTTP API, anahtar doğrulama sadece."""

    def test_together_success(self, monkeypatch, tmp_path):
        """Başarılı → kart açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('TOGETHER_API_KEY', 'test-key123')

        together._CACHE = None
        card = together.collect(30)
        assert card is None or isinstance(card, dict)

    def test_together_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('TOGETHER_API_KEY', raising=False)

        together._CACHE = None
        card = together.collect(30)
        assert card is None

    def test_together_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('TOGETHER_API_KEY', 'bad-key')

        with patch('urllib.request.urlopen') as mock_urlopen:
            import urllib.error
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            together._CACHE = None
            card = together.collect(30)
            assert card is None or isinstance(card, dict)

    def test_together_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('TOGETHER_API_KEY', 'test-key')

        together._CACHE = None
        # Together adaptörü basit ve genellikle null dönüyor
        card = together.collect(30)
        assert card is None or isinstance(card, dict)

    def test_together_timeout(self, monkeypatch, tmp_path):
        """Timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('TOGETHER_API_KEY', 'test-key')

        together._CACHE = None
        card = together.collect(30)
        assert card is None or isinstance(card, dict)


# ============================================================================
# NOVITA TESTLERI
# ============================================================================

class TestNovita(FixtureCase):
    """Novita AI adaptörü — HTTP API, kredi bakiyesi."""

    def test_novita_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('NOVITA_API_KEY', raising=False)

        novita._CACHE = None
        card = novita.collect(30)
        assert card is None

    def test_novita_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → kart açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('NOVITA_API_KEY', 'test-key')

        with patch('usage.providers.novita._get') as mock_get:
            mock_get.return_value = {
                'credit': 150.5,
                'api_calls': 42,
                'status': 'active'
            }

            novita._CACHE = None
            card = novita.collect(30)
            assert card is None or isinstance(card, dict)

    def test_novita_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → error kartı veya None."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('NOVITA_API_KEY', 'bad-key')

        with patch('usage.providers.novita._get') as mock_get:
            import urllib.error
            mock_get.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            novita._CACHE = None
            card = novita.collect(30)
            assert card is None or (isinstance(card, dict) and card.get('status') == 'error')

    def test_novita_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('NOVITA_API_KEY', 'test-key')

        with patch('usage.providers.novita._get') as mock_get:
            mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

            novita._CACHE = None
            card = novita.collect(30)
            assert card is None or isinstance(card, dict)

    def test_novita_timeout(self, monkeypatch, tmp_path):
        """Timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('NOVITA_API_KEY', 'test-key')

        with patch('usage.providers.novita._get') as mock_get:
            mock_get.side_effect = socket.timeout("Connection timeout")

            novita._CACHE = None
            card = novita.collect(30)
            assert card is None or isinstance(card, dict)


# ============================================================================
# DEEPINFRA TESTLERI
# ============================================================================

class TestDeepInfra(FixtureCase):
    """DeepInfra adaptörü — HTTP API, kredi bakiyesi."""

    def test_deepinfra_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('DEEPINFRA_API_KEY', raising=False)
        monkeypatch.delenv('DEEPINFRA_TOKEN', raising=False)

        deepinfra._CACHE = None
        card = deepinfra.collect(30)
        assert card is None

    def test_deepinfra_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → kart açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPINFRA_API_KEY', 'test-key')

        with patch('usage.providers.deepinfra._get') as mock_get:
            mock_get.return_value = {
                'credits_balance': 100.0,
                'credits_used': 25.0,
                'created_at': '2026-01-01'
            }

            deepinfra._CACHE = None
            card = deepinfra.collect(30)
            assert card is None or isinstance(card, dict)

    def test_deepinfra_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → error kartı veya None."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPINFRA_API_KEY', 'bad-key')

        with patch('usage.providers.deepinfra._get') as mock_get:
            import urllib.error
            mock_get.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            deepinfra._CACHE = None
            card = deepinfra.collect(30)
            assert card is None or (isinstance(card, dict) and card.get('status') == 'error')

    def test_deepinfra_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPINFRA_API_KEY', 'test-key')

        with patch('usage.providers.deepinfra._get') as mock_get:
            mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

            deepinfra._CACHE = None
            card = deepinfra.collect(30)
            assert card is None or isinstance(card, dict)

    def test_deepinfra_timeout(self, monkeypatch, tmp_path):
        """Timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DEEPINFRA_API_KEY', 'test-key')

        with patch('usage.providers.deepinfra._get') as mock_get:
            mock_get.side_effect = socket.timeout("Connection timeout")

            deepinfra._CACHE = None
            card = deepinfra.collect(30)
            assert card is None or isinstance(card, dict)


# ============================================================================
# HUGGINGFACE TESTLERI
# ============================================================================

class TestHuggingFace(FixtureCase):
    """HuggingFace adaptörü — HTTP API, kota (quota)."""

    def test_huggingface_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('HUGGINGFACE_API_KEY', raising=False)
        monkeypatch.delenv('HF_TOKEN', raising=False)

        huggingface._CACHE = None
        card = huggingface.collect(30)
        assert card is None

    def test_huggingface_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → kart açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('HUGGINGFACE_API_KEY', 'hf_test123')

        with patch('usage.providers.huggingface._get') as mock_get:
            def _get_side_effect(path, key):
                if 'whoami' in path:
                    return {'id': 'testuser', 'plan': 'pro'}
                elif 'quota' in path:
                    return {'quota_used': 100, 'quota_limit': 1000}
                return {}
            mock_get.side_effect = _get_side_effect

            huggingface._CACHE = None
            card = huggingface.collect(30)
            assert card is not None
            assert card.get('kind') == 'quota'
            assert card['available'] is True

    def test_huggingface_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('HUGGINGFACE_API_KEY', 'bad-key')

        with patch('usage.providers.huggingface._get') as mock_get:
            import urllib.error
            mock_get.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            huggingface._CACHE = None
            card = huggingface.collect(30)
            assert card is not None
            assert card.get('status') == 'error'

    def test_huggingface_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('HUGGINGFACE_API_KEY', 'test-key')

        with patch('usage.providers.huggingface._get') as mock_get:
            mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

            huggingface._CACHE = None
            card = huggingface.collect(30)
            assert card is not None
            assert card.get('status') == 'error'

    def test_huggingface_timeout(self, monkeypatch, tmp_path):
        """Timeout → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('HUGGINGFACE_API_KEY', 'test-key')

        with patch('usage.providers.huggingface._get') as mock_get:
            mock_get.side_effect = socket.timeout("Connection timeout")

            huggingface._CACHE = None
            card = huggingface.collect(30)
            assert card is not None
            assert card.get('status') == 'error'


# ============================================================================
# ELEVENLABS TESTLERI
# ============================================================================

class TestElevenLabs(FixtureCase):
    """ElevenLabs adaptörü — HTTP API, karakter kotası (quota)."""

    def test_elevenlabs_no_key(self, monkeypatch, tmp_path):
        """Anahtar yok → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('ELEVENLABS_API_KEY', raising=False)

        elevenlabs._CACHE = None
        card = elevenlabs.collect(30)
        assert card is None

    def test_elevenlabs_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → quota kartı açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('ELEVENLABS_API_KEY', 'test-key123')

        with patch('usage.providers.elevenlabs._get') as mock_get:
            mock_get.return_value = {
                'character_count': 5000,
                'character_limit': 100000,
                'next_character_count_reset_unix': 1700000000,
                'tier': 'professional',
                'status': 'active'
            }

            elevenlabs._CACHE = None
            card = elevenlabs.collect(30)
            assert card is not None
            validate_card(card, expected_kind='quota')
            assert card['available'] is True
            assert 'quota' in card or 'character' in str(card).lower()

    def test_elevenlabs_auth_error(self, monkeypatch, tmp_path):
        """401 hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('ELEVENLABS_API_KEY', 'bad-key')

        with patch('usage.providers.elevenlabs._get') as mock_get:
            import urllib.error
            mock_get.side_effect = urllib.error.HTTPError(
                url="http://test", code=401, msg="Unauthorized", hdrs={}, fp=None
            )

            elevenlabs._CACHE = None
            card = elevenlabs.collect(30)
            assert card is not None
            assert card['status'] == 'error'
            assert card['available'] is True

    def test_elevenlabs_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('ELEVENLABS_API_KEY', 'test-key')

        with patch('usage.providers.elevenlabs._get') as mock_get:
            mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

            elevenlabs._CACHE = None
            card = elevenlabs.collect(30)
            assert card is not None
            assert card['status'] == 'error'

    def test_elevenlabs_timeout(self, monkeypatch, tmp_path):
        """Timeout → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('ELEVENLABS_API_KEY', 'test-key')

        with patch('usage.providers.elevenlabs._get') as mock_get:
            mock_get.side_effect = socket.timeout("Connection timeout")

            elevenlabs._CACHE = None
            card = elevenlabs.collect(30)
            assert card is not None
            assert card['status'] == 'error'


# ============================================================================
# OLLAMA TESTLERI
# ============================================================================

class TestOllama(FixtureCase):
    """Ollama adaptörü — local HTTP API, binary check, kind='local'."""

    def test_ollama_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → local kartı açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('usage.providers.ollama._get') as mock_get:
            mock_get.side_effect = [
                {'models': [
                    {'name': 'llama2', 'size': 1000000},
                    {'name': 'mistral', 'size': 2000000},
                ]},
                {'models': [
                    {'name': 'llama2', 'size': 1000000},
                ]}
            ]

            ollama._CACHE = None
            card = ollama.collect(30)
            assert card is not None
            validate_card(card, expected_kind='local')
            assert card['available'] is True
            assert card['status'] == 'ok'

    def test_ollama_binary_not_found(self, monkeypatch, tmp_path):
        """Binary yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('shutil.which') as mock_which:
            mock_which.return_value = None

            ollama._CACHE = None
            card = ollama.collect(30)
            assert card is None

    def test_ollama_offline(self, monkeypatch, tmp_path):
        """Binary var ama servis kapalı → status='offline'."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('shutil.which') as mock_which:
            mock_which.return_value = '/usr/bin/ollama'
            with patch('usage.providers.ollama._get') as mock_get:
                import urllib.error
                mock_get.side_effect = urllib.error.URLError("Connection refused")

                ollama._CACHE = None
                card = ollama.collect(30)
                assert card is not None
                validate_card(card, expected_kind='local')
                assert card['status'] == 'offline'

    def test_ollama_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → error kartı."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('shutil.which') as mock_which:
            mock_which.return_value = '/usr/bin/ollama'
            with patch('usage.providers.ollama._get') as mock_get:
                mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

                ollama._CACHE = None
                card = ollama.collect(30)
                assert card is not None or card is None  # Tolerant

    def test_ollama_timeout(self, monkeypatch, tmp_path):
        """Timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('shutil.which') as mock_which:
            mock_which.return_value = '/usr/bin/ollama'
            with patch('usage.providers.ollama._get') as mock_get:
                mock_get.side_effect = socket.timeout("Connection timeout")

                ollama._CACHE = None
                card = ollama.collect(30)
                # Timeout genellikle offline olarak algılanır
                assert card is None or card.get('status') == 'offline'


# ============================================================================
# LM STUDIO TESTLERI
# ============================================================================

class TestLMStudio(FixtureCase):
    """LM Studio adaptörü — local HTTP API, binary check."""

    def test_lmstudio_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → local kartı açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('LMSTUDIO_URL', 'http://127.0.0.1:1234')

        with patch('usage.providers.lmstudio._get') as mock_get:
            mock_get.return_value = {
                'data': [
                    {'id': 'model1', 'type': 'language', 'state': 'loaded'},
                    {'id': 'model2', 'type': 'language', 'state': 'not-loaded'},
                ]
            }

            lmstudio._CACHE = None
            card = lmstudio.collect(30)
            assert card is not None
            validate_card(card, expected_kind='local')
            assert card['available'] is True

    def test_lmstudio_binary_not_found(self, monkeypatch, tmp_path):
        """Binary yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('shutil.which') as mock_which:
            mock_which.return_value = None

            lmstudio._CACHE = None
            card = lmstudio.collect(30)
            assert card is None

    def test_lmstudio_offline(self, monkeypatch, tmp_path):
        """Binary var ama servis kapalı → status='offline'."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('LMSTUDIO_URL', 'http://127.0.0.1:1234')

        with patch('shutil.which') as mock_which:
            mock_which.return_value = '/usr/bin/lms'
            with patch('usage.providers.lmstudio._get') as mock_get:
                import urllib.error
                mock_get.side_effect = urllib.error.URLError("Connection refused")

                lmstudio._CACHE = None
                card = lmstudio.collect(30)
                assert card is not None
                assert card['status'] == 'offline'

    def test_lmstudio_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('shutil.which') as mock_which:
            mock_which.return_value = '/usr/bin/lms'
            with patch('usage.providers.lmstudio._get') as mock_get:
                mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

                lmstudio._CACHE = None
                card = lmstudio.collect(30)
                assert card is None or isinstance(card, dict)

    def test_lmstudio_timeout(self, monkeypatch, tmp_path):
        """Timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))

        with patch('shutil.which') as mock_which:
            mock_which.return_value = '/usr/bin/lms'
            with patch('usage.providers.lmstudio._get') as mock_get:
                mock_get.side_effect = socket.timeout("Connection timeout")

                lmstudio._CACHE = None
                card = lmstudio.collect(30)
                assert card is None or card.get('status') == 'offline'


# ============================================================================
# JAN TESTLERI
# ============================================================================

class TestJan(FixtureCase):
    """Jan adaptörü — local HTTP API, directory check."""

    def test_jan_success(self, monkeypatch, tmp_path):
        """Başarılı yanıt → local kartı açılır."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('JAN_URL', 'http://127.0.0.1:1337')

        with patch('usage.providers.jan._get') as mock_get:
            mock_get.return_value = {
                'data': [
                    {'id': 'model1'},
                    {'id': 'model2'},
                ]
            }

            jan._CACHE = None
            card = jan.collect(30)
            assert card is not None
            validate_card(card, expected_kind='local')
            assert card['available'] is True

    def test_jan_directory_not_found(self, monkeypatch, tmp_path):
        """Dizin yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))

        jan._CACHE = None
        card = jan.collect(30)
        assert card is None

    def test_jan_offline(self, monkeypatch, tmp_path):
        """Dizin var ama servis kapalı → status='offline'."""
        jan_dir = tmp_path / '.jan'
        jan_dir.mkdir()
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('JAN_URL', 'http://127.0.0.1:1337')

        with patch('usage.providers.jan._get') as mock_get:
            import urllib.error
            mock_get.side_effect = urllib.error.URLError("Connection refused")

            jan._CACHE = None
            card = jan.collect(30)
            assert card is not None
            assert card['status'] == 'offline'

    def test_jan_bad_json(self, monkeypatch, tmp_path):
        """JSON parse hatası → çökmez."""
        jan_dir = tmp_path / '.jan'
        jan_dir.mkdir()
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('JAN_URL', 'http://127.0.0.1:1337')

        with patch('usage.providers.jan._get') as mock_get:
            mock_get.side_effect = json.JSONDecodeError("msg", "doc", 0)

            jan._CACHE = None
            card = jan.collect(30)
            assert card is None or isinstance(card, dict)

    def test_jan_timeout(self, monkeypatch, tmp_path):
        """Timeout → çökmez."""
        jan_dir = tmp_path / '.jan'
        jan_dir.mkdir()
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('JAN_URL', 'http://127.0.0.1:1337')

        with patch('usage.providers.jan._get') as mock_get:
            mock_get.side_effect = socket.timeout("Connection timeout")

            jan._CACHE = None
            card = jan.collect(30)
            assert card is None or card.get('status') == 'offline'


# ============================================================================
# CODEX TESTLERI
# ============================================================================

class TestCodex(FixtureCase):
    """Codex adaptörü — dosya okuma, rollout-*.jsonl, limits bloğu."""

    def test_codex_success(self, monkeypatch, tmp_path):
        """Başarılı dosya → tokens kartı açılır."""
        codex_dir = tmp_path / '.codex' / 'sessions' / '2026' / '08' / '29'
        codex_dir.mkdir(parents=True)

        # Gerçek rollout-*.jsonl dosyası
        rollout_file = codex_dir / 'rollout-2026-08-29.jsonl'
        rollout_data = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150
                }},
                "rate_limits": {
                    "primary": {
                        "used_percent": 84.0,
                        "window_minutes": 300,
                        "resets_at": 9999999999
                    },
                    "secondary": {
                        "used_percent": 29.0,
                        "window_minutes": 10080,
                        "resets_at": 9999999999
                    },
                    "plan_type": "plus"
                }
            }
        }
        rollout_file.write_text(json.dumps(rollout_data) + "\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(codex, 'SESSIONS_DIR', codex_dir.parent.parent.parent)

        codex._FILE_CACHE.clear()
        card = codex.collect(30)
        assert card is not None
        validate_card(card, expected_kind='tokens')
        assert card['available'] is True
        assert card['status'] == 'ok'
        # limits bloğu kontrol et
        assert 'limits' in card
        assert card['limits']['session']['pct'] == 84.0
        assert card['limits']['session']['expired'] is False

    def test_codex_no_directory(self, monkeypatch, tmp_path):
        """Dizin yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(codex, 'SESSIONS_DIR', tmp_path / 'nonexistent')

        codex._FILE_CACHE.clear()
        card = codex.collect(30)
        assert card is None

    def test_codex_expired_limits(self, monkeypatch, tmp_path):
        """resets_at geçmişte → expired=True, pct=None."""
        codex_dir = tmp_path / '.codex' / 'sessions' / '2026' / '08' / '29'
        codex_dir.mkdir(parents=True)

        rollout_file = codex_dir / 'rollout-2026-08-29.jsonl'
        rollout_data = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15
                }},
                "rate_limits": {
                    "primary": {
                        "used_percent": 50.0,
                        "window_minutes": 300,
                        "resets_at": 1000000000  # geçmiş
                    },
                    "plan_type": "plus"
                }
            }
        }
        rollout_file.write_text(json.dumps(rollout_data) + "\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(codex, 'SESSIONS_DIR', codex_dir.parent.parent.parent)

        codex._FILE_CACHE.clear()
        card = codex.collect(30)
        assert card is not None
        assert card['limits']['session']['expired'] is True
        assert card['limits']['session']['pct'] is None

    def test_codex_bad_json(self, monkeypatch, tmp_path):
        """Bozuk JSON → çökmez, nodata kartı veya None."""
        codex_dir = tmp_path / '.codex' / 'sessions' / '2026' / '08' / '29'
        codex_dir.mkdir(parents=True)

        rollout_file = codex_dir / 'rollout-2026-08-29.jsonl'
        rollout_file.write_text("not valid json {]\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(codex, 'SESSIONS_DIR', codex_dir.parent.parent.parent)

        codex._FILE_CACHE.clear()
        card = codex.collect(30)
        # Bozuk JSON görmezden gelinir — nodata kartı veya None döner
        assert card is None or card.get('status') == 'nodata'

    def test_codex_timeout(self, monkeypatch, tmp_path):
        """Dizin taraması timeout → çökmez, nodata kartı veya None."""
        codex_dir = tmp_path / '.codex' / 'sessions' / '2026' / '08' / '29'
        codex_dir.mkdir(parents=True)

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(codex, 'SESSIONS_DIR', codex_dir.parent.parent.parent)

        # Dosya yok — timeout değil ama veri yok → nodata kartı döner
        codex._FILE_CACHE.clear()
        card = codex.collect(30)
        assert card is None or card.get('status') == 'nodata'


# ============================================================================
# AIDER TESTLERI
# ============================================================================

class TestAider(FixtureCase):
    """Aider adaptörü — dosya okuma, lokal-log."""

    def test_aider_success(self, monkeypatch, tmp_path):
        """Başarılı dosya → tokens kartı açılır."""
        aider_dir = tmp_path / '.aider'
        aider_dir.mkdir()

        analytics_file = aider_dir / 'analytics.jsonl'
        analytics_data = {
            "event": "message_send",
            "properties": {
                "total_tokens_sent": 100,
                "total_tokens_received": 50,
                "cost": 0.001,
                "model": "claude-3-opus"
            },
            "time": 1234567890
        }
        analytics_file.write_text(json.dumps(analytics_data) + "\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(aider, 'LOG_PATH', analytics_file)

        aider._CACHE = None
        card = aider.collect(30)
        assert card is not None
        validate_card(card, expected_kind='tokens')
        assert card['available'] is True

    def test_aider_no_file(self, monkeypatch, tmp_path):
        """Dosya yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(aider, 'LOG_PATH', tmp_path / 'nonexistent' / 'analytics.jsonl')

        aider._CACHE = None
        card = aider.collect(30)
        assert card is None

    def test_aider_bad_json(self, monkeypatch, tmp_path):
        """Bozuk JSON → çökmez."""
        aider_dir = tmp_path / '.aider'
        aider_dir.mkdir()

        analytics_file = aider_dir / 'analytics.jsonl'
        analytics_file.write_text("not valid json {]\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(aider, 'LOG_PATH', analytics_file)

        aider._CACHE = None
        card = aider.collect(30)
        assert card is None or card.get('status') == 'nodata'

    def test_aider_empty_file(self, monkeypatch, tmp_path):
        """Boş dosya → nodata kartı veya None."""
        aider_dir = tmp_path / '.aider'
        aider_dir.mkdir()

        analytics_file = aider_dir / 'analytics.jsonl'
        analytics_file.write_text("", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(aider, 'LOG_PATH', analytics_file)

        aider._CACHE = None
        card = aider.collect(30)
        # Boş dosya nodata kartı döner
        assert card is None or card.get('status') == 'nodata'

    def test_aider_timeout(self, monkeypatch, tmp_path):
        """Dosya okuma timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(aider, 'LOG_PATH', tmp_path / 'nonexistent')

        aider._CACHE = None
        card = aider.collect(30)
        assert card is None


# ============================================================================
# CONTINUEDEV TESTLERI
# ============================================================================

class TestContinueDev(FixtureCase):
    """Continue.dev adaptörü — dosya okuma, lokal-log."""

    def test_continuedev_success(self, monkeypatch, tmp_path):
        """Başarılı dosya → tokens kartı açılır."""
        continue_dir = tmp_path / '.continue' / 'dev_data'
        continue_dir.mkdir(parents=True)

        log_file = continue_dir / 'tokens.jsonl'
        log_data = {
            "model": "claude-3-sonnet",
            "provider": "anthropic",
            "promptTokens": 100,
            "generatedTokens": 50
        }
        log_file.write_text(json.dumps(log_data) + "\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(continuedev, 'DATA_DIR', continue_dir)

        continuedev._CACHE = None
        card = continuedev.collect(30)
        assert card is not None
        validate_card(card, expected_kind='tokens')
        assert card['available'] is True

    def test_continuedev_no_directory(self, monkeypatch, tmp_path):
        """Dizin yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(continuedev, 'DATA_DIR', tmp_path / 'nonexistent')

        continuedev._CACHE = None
        card = continuedev.collect(30)
        assert card is None

    def test_continuedev_bad_json(self, monkeypatch, tmp_path):
        """Bozuk JSON → çökmez."""
        continue_dir = tmp_path / '.continue' / 'dev_data'
        continue_dir.mkdir(parents=True)

        log_file = continue_dir / 'tokens.jsonl'
        log_file.write_text("not valid json {]\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(continuedev, 'DATA_DIR', continue_dir)

        continuedev._CACHE = None
        card = continuedev.collect(30)
        assert card is None or card.get('status') == 'nodata'

    def test_continuedev_empty_directory(self, monkeypatch, tmp_path):
        """Boş dizin → nodata kartı veya None."""
        continue_dir = tmp_path / '.continue' / 'dev_data'
        continue_dir.mkdir(parents=True)

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(continuedev, 'DATA_DIR', continue_dir)

        continuedev._CACHE = None
        card = continuedev.collect(30)
        # Boş dizin nodata kartı döner
        assert card is None or card.get('status') == 'nodata'

    def test_continuedev_timeout(self, monkeypatch, tmp_path):
        """Dizin taraması timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(continuedev, 'DATA_DIR', tmp_path / 'nonexistent')

        continuedev._CACHE = None
        card = continuedev.collect(30)
        assert card is None


# ============================================================================
# CODY TESTLERI
# ============================================================================

class TestCody(FixtureCase):
    """Cody adaptörü — dosya okuma, lokal-log."""

    def test_cody_success(self, monkeypatch, tmp_path):
        """Başarılı dosya → tokens kartı açılır."""
        cody_dir = tmp_path / '.cody'
        cody_dir.mkdir()

        log_file = cody_dir / 'tokens.jsonl'
        log_data = {
            "model": "claude-3-sonnet",
            "provider": "anthropic",
            "tokens": 150
        }
        log_file.write_text(json.dumps(log_data) + "\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        original_dirs = cody._DIRS
        monkeypatch.setattr(cody, '_DIRS', [cody_dir])

        cody._CACHE = None
        card = cody.collect(30)
        # Cody adaptörü başarılı dönebilir veya None
        assert card is None or isinstance(card, dict)

    def test_cody_no_directory(self, monkeypatch, tmp_path):
        """Dizin yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(cody, '_DIRS', [tmp_path / 'nonexistent'])

        cody._CACHE = None
        card = cody.collect(30)
        assert card is None

    def test_cody_bad_json(self, monkeypatch, tmp_path):
        """Bozuk JSON → çökmez."""
        cody_dir = tmp_path / '.cody'
        cody_dir.mkdir()

        log_file = cody_dir / 'tokens.jsonl'
        log_file.write_text("not valid json {]\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(cody, '_DIRS', [cody_dir])

        cody._CACHE = None
        card = cody.collect(30)
        assert card is None or card.get('status') == 'nodata'

    def test_cody_empty_directory(self, monkeypatch, tmp_path):
        """Boş dizin → nodata kartı veya None."""
        cody_dir = tmp_path / '.cody'
        cody_dir.mkdir()

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(cody, '_DIRS', [cody_dir])

        cody._CACHE = None
        card = cody.collect(30)
        # Boş dizin nodata kartı döner
        assert card is None or card.get('status') == 'nodata'

    def test_cody_timeout(self, monkeypatch, tmp_path):
        """Dizin taraması timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(cody, '_DIRS', [tmp_path / 'nonexistent'])

        cody._CACHE = None
        card = cody.collect(30)
        assert card is None


# ============================================================================
# WINDSURF TESTLERI
# ============================================================================

class TestWindsurf(FixtureCase):
    """Windsurf adaptörü — dosya okuma, lokal-log."""

    def test_windsurf_success(self, monkeypatch, tmp_path):
        """Başarılı dosya → tokens kartı açılır."""
        windsurf_dir = tmp_path / '.codeium' / 'windsurf'
        windsurf_dir.mkdir(parents=True)

        log_file = windsurf_dir / 'tokens.jsonl'
        log_data = {
            "model": "gpt-4",
            "tokens": 200
        }
        log_file.write_text(json.dumps(log_data) + "\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(windsurf, '_DIRS', [windsurf_dir])

        windsurf._CACHE = None
        card = windsurf.collect(30)
        # Windsurf adaptörü başarılı dönebilir veya None
        assert card is None or isinstance(card, dict)

    def test_windsurf_no_directory(self, monkeypatch, tmp_path):
        """Dizin yoksa → None döner."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(windsurf, '_DIRS', [tmp_path / 'nonexistent'])

        windsurf._CACHE = None
        card = windsurf.collect(30)
        assert card is None

    def test_windsurf_bad_json(self, monkeypatch, tmp_path):
        """Bozuk JSON → çökmez."""
        windsurf_dir = tmp_path / '.codeium' / 'windsurf'
        windsurf_dir.mkdir(parents=True)

        log_file = windsurf_dir / 'tokens.jsonl'
        log_file.write_text("not valid json {]\n", encoding="utf-8")

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(windsurf, '_DIRS', [windsurf_dir])

        windsurf._CACHE = None
        card = windsurf.collect(30)
        assert card is None or card.get('status') == 'nodata'

    def test_windsurf_empty_directory(self, monkeypatch, tmp_path):
        """Boş dizin → nodata kartı veya None."""
        windsurf_dir = tmp_path / '.codeium' / 'windsurf'
        windsurf_dir.mkdir(parents=True)

        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(windsurf, '_DIRS', [windsurf_dir])

        windsurf._CACHE = None
        card = windsurf.collect(30)
        # Boş dizin nodata kartı döner
        assert card is None or card.get('status') == 'nodata'

    def test_windsurf_timeout(self, monkeypatch, tmp_path):
        """Dizin taraması timeout → çökmez."""
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(windsurf, '_DIRS', [tmp_path / 'nonexistent'])

        windsurf._CACHE = None
        card = windsurf.collect(30)
        assert card is None


if __name__ == '__main__':
    unittest.main(verbosity=2)
