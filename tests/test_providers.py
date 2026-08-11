"""Provider adapters — the cards, and the numbers on them.

FAZ 3 was meant to verify the five "candidate" adapters against live keys. There are no
keys on this machine: `~/.config/usage-tracker/env` exists but every line in it is a
comment, and the shell has none either. So the endpoints were probed **unauthenticated**
instead, on 2026-08-11. A 401 proves the URL exists and wants a key; a 404 proves the
adapter would never have worked, key or no key.

    deepseek    /user/balance                401  exists
    openrouter  /api/v1/key, /api/v1/credits 401  exists
    elevenlabs  /v1/user/subscription        401  exists
    openai      /v1/organization/costs       401  exists
    huggingface /api/whoami-v2, /api/quota   401  exists  (quota was marked "candidate")
    deepinfra   /v1/me                       401  exists
                /api/v1/user, /v1/user       404  dead
    novita      /v3/user                     400  exists
                /api/v1/user/balance, /v1/user 404 dead
    together    /v1/models                   401  base is right
                7 account/balance paths      404  Together publishes no such endpoint

These tests pin what the probe found, so a dead path cannot quietly come back, and they
cover the field-scanning bug the probe led to.
"""
import unittest
from unittest import mock

from usage.providers import deepinfra, novita, together
from usage.providers import _money


class AmountScanningIsStrict(unittest.TestCase):
    """The three credit adapters shared a `_dig()` that walked into every nested dict and
    returned the first field named balance/credit/remaining/available it found.

    Two ways that produces a confident wrong number:
      `{"available": true}`            -> float(True) is 1.0 -> "$1.00 remaining"
      `{"rate_limit": {"remaining": 4999}}` -> "$4999.00 remaining"

    Neither would look like an error to anyone.
    """

    def test_a_boolean_is_not_an_amount(self):
        self.assertIsNone(_money.pick_amount({'available': True}))
        self.assertIsNone(_money.pick_amount({'available': False}))

    def test_a_rate_limit_counter_is_not_a_balance(self):
        payload = {'rate_limit': {'remaining': 4999, 'limit': 5000}}
        self.assertIsNone(_money.pick_amount(payload),
                          'a rate-limit counter was read as a dollar balance')

    def test_a_plain_top_level_balance_is_read(self):
        self.assertEqual(_money.pick_amount({'balance': 12.5}), 12.5)

    def test_a_balance_inside_a_known_envelope_is_read(self):
        """Real APIs wrap the payload — {"data": {...}} — and that has to keep working."""
        for envelope in ('data', 'result', 'account', 'user'):
            with self.subTest(envelope=envelope):
                self.assertEqual(_money.pick_amount({envelope: {'credit': 7.25}}), 7.25)

    def test_numeric_strings_are_accepted(self):
        self.assertEqual(_money.pick_amount({'balance': '3.50'}), 3.5)

    def test_junk_is_rejected_rather_than_guessed(self):
        for payload in ({'balance': 'lots'}, {'balance': None}, {'nothing': 1},
                        {'balance': []}, 'not a dict', None, {'balance': float('nan')}):
            with self.subTest(payload=payload):
                self.assertIsNone(_money.pick_amount(payload))

    def test_a_negative_amount_is_kept(self):
        """An overdrawn account is real; clamping it to zero would hide a problem."""
        self.assertEqual(_money.pick_amount({'balance': -4.2}), -4.2)


class OnlyProbedPathsAreTried(unittest.TestCase):
    """Every 404 path in the list above is a request that costs a round trip, then hands
    an HTML error page to a JSON parser. They were never going to work."""

    DEAD = {
        deepinfra: ('/api/v1/user', '/v1/user'),
        novita: ('/api/v1/user/balance', '/v1/user'),
        together: ('/v1/account', '/api/account', '/v1/user'),
    }

    def test_no_adapter_still_requests_a_path_the_probe_found_missing(self):
        import inspect
        for module, dead_paths in self.DEAD.items():
            src = inspect.getsource(module)
            for path in dead_paths:
                with self.subTest(module=module.PROVIDER_ID, path=path):
                    self.assertNotIn(f"'{path}'", src,
                                     f'{module.PROVIDER_ID} still tries {path}, measured 404')


class NoKeyNoCard(unittest.TestCase):
    """'No dead cards': an adapter with nothing configured returns None so the UI does not
    open an empty card. Tested on every adapter, because a runner has no keys at all and
    this is the code path every CI run takes."""

    def test_every_adapter_returns_none_without_configuration(self):
        import os
        from usage import providers
        with mock.patch.dict(os.environ, {}, clear=True):
            for module in providers._ADAPTERS:
                if not hasattr(module, '_key'):
                    continue           # local-file adapters key off directories, not env
                with self.subTest(provider=module.PROVIDER_ID):
                    module._CACHE = None
                    self.assertIsNone(module.collect(30),
                                      f'{module.PROVIDER_ID} opened a card with no key')


class TogetherTellsTheTruth(unittest.TestCase):
    """Together AI's API base is real (`/v1/models` -> 401 "Missing API key") but it
    publishes no account or balance endpoint: seven candidate paths all returned 404 with
    an HTML page. The adapter claimed `kind: 'spend'` and scanned for a balance that does
    not exist anywhere in the vendor's API."""

    def setUp(self):
        together._CACHE = None
        self.addCleanup(setattr, together, '_CACHE', None)

    def _card(self, get_side_effect):
        with mock.patch.object(together, '_key', return_value='sk-test'), \
                mock.patch.object(together, '_get', side_effect=get_side_effect):
            return together.collect(30)

    def test_a_valid_key_produces_an_honest_card_not_a_fake_balance(self):
        card = self._card(lambda path, key: {'data': [{'id': 'meta-llama/x'}]})

        self.assertIsNotNone(card)
        self.assertNotIn('balance', card,
                         'the card shows a balance Together does not publish')
        self.assertEqual(card['status'], 'nodata')
        self.assertTrue(card.get('note'), 'the card does not say why it is empty')

    def test_an_invalid_key_says_so(self):
        import urllib.error

        def boom(path, key):
            raise urllib.error.HTTPError(path, 401, 'Unauthorized', {}, None)

        card = self._card(boom)
        self.assertEqual(card['status'], 'error')
        self.assertIn('401', card['error'])


class GarbageDoesNotCrashOrLie(unittest.TestCase):
    """These endpoints returned HTML when they were wrong. An adapter that meets HTML, or
    a truncated body, or a shape nobody expected, must produce no card rather than a
    number — and must never take the whole registry down with it."""

    PAYLOADS = [
        {'html': '<!DOCTYPE html>'},
        {'detail': 'Not Found'},
        {'data': {'unrelated': 'text'}},
        {},
        [],
        {'rate_limit': {'remaining': 9999}},
    ]

    def test_credit_adapters_report_no_balance_for_unrecognised_shapes(self):
        for module in (deepinfra, novita):
            for payload in self.PAYLOADS:
                with self.subTest(provider=module.PROVIDER_ID, payload=payload):
                    module._CACHE = None
                    with mock.patch.object(module, '_key', return_value='k'), \
                            mock.patch.object(module, '_get', return_value=payload):
                        card = module.collect(30)
                    self.assertIsNotNone(card)
                    self.assertNotIn('balance', card,
                                     f'{module.PROVIDER_ID} invented a balance from {payload}')
                    self.assertEqual(card['status'], 'error')

    def test_a_recognisable_balance_is_still_read(self):
        """The hardening must not swing the other way and reject real data."""
        for module in (deepinfra, novita):
            with self.subTest(provider=module.PROVIDER_ID):
                module._CACHE = None
                with mock.patch.object(module, '_key', return_value='k'), \
                        mock.patch.object(module, '_get', return_value={'balance': 21.0}):
                    card = module.collect(30)
                self.assertEqual(card['status'], 'ok')
                self.assertEqual(card['balance']['remaining'], 21.0)

    def test_one_exploding_adapter_does_not_take_the_registry_down(self):
        from usage import providers
        with mock.patch.object(novita, 'collect', side_effect=RuntimeError('boom')):
            cards = providers.collect(30)
        self.assertIsInstance(cards, list)


if __name__ == '__main__':
    unittest.main()
