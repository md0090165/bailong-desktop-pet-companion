import json
import ssl
import unittest
from urllib.error import URLError
from unittest.mock import patch, Mock

import jwt
from chat_connection import validate_id_token, ConnectionFailure
from login_diagnostics import error_details


class IdentityDiagnosticTests(unittest.TestCase):
    def test_jwks_tls_chain_is_preserved_without_url_token_or_message(self):
        tls = ssl.SSLCertVerificationError(1, 'SECRET PRIVATE URL AND TOKEN')
        network = URLError(tls)
        failure = jwt.PyJWKClientConnectionError('SECRET')
        failure.__cause__ = network
        with patch('chat_connection.jwt.PyJWKClient') as client:
            client.return_value.get_signing_key_from_jwt.side_effect = failure
            with self.assertRaises(ConnectionFailure) as caught:
                validate_id_token('SECRET_TOKEN', 'SECRET_ID')
        details = error_details(caught.exception)
        self.assertEqual(details['identity_reason'], 'signing_key_download')
        self.assertEqual(details['cause']['cause']['reason']['exception'], 'SSLCertVerificationError')
        self.assertNotIn('SECRET', json.dumps(details))

    def test_individual_jwt_failures_remain_rejected_and_identifiable(self):
        cases = [(jwt.ExpiredSignatureError('SECRET'), 'token_expired'),
                 (jwt.ImmatureSignatureError('SECRET'), 'token_not_yet_valid'),
                 (jwt.InvalidAudienceError('SECRET'), 'audience_mismatch'),
                 (jwt.InvalidIssuerError('SECRET'), 'issuer_mismatch'),
                 (jwt.InvalidSignatureError('SECRET'), 'signature_invalid'),
                 (jwt.InvalidAlgorithmError('SECRET'), 'algorithm_invalid'),
                 (jwt.MissingRequiredClaimError('SECRET'), 'required_claim_missing')]
        for error, expected in cases:
            with self.subTest(expected=expected), patch('chat_connection.jwt.PyJWKClient'), patch('chat_connection.jwt.decode', side_effect=error):
                with self.assertRaises(ConnectionFailure) as caught:
                    validate_id_token('fake', 'id')
                details = error_details(caught.exception)
                self.assertEqual(details['identity_reason'], expected)
                self.assertNotIn('SECRET', json.dumps(details))

    def test_nonce_and_account_mismatch_are_distinct_and_still_rejected(self):
        for nonce, subject, expected in [('wrong', 'same', 'nonce_mismatch'), ('ok', 'wrong', 'account_mismatch')]:
            with patch('chat_connection.jwt.PyJWKClient'), patch('chat_connection.jwt.decode', return_value={'sub': 'same', 'nonce': 'ok'}):
                with self.assertRaises(ConnectionFailure) as caught:
                    validate_id_token('fake', 'id', nonce, subject)
                self.assertEqual(caught.exception.identity_reason, expected)

    def test_urLError_text_and_cause_cycles_cannot_leak_or_recurse_forever(self):
        error = URLError('SECRET_URL')
        self.assertNotIn('SECRET', json.dumps(error_details(error)))
        error.__cause__ = error
        self.assertLess(len(json.dumps(error_details(error))), 500)


if __name__ == '__main__':
    unittest.main()
