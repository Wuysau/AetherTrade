"""Process-local identity index of server-verified, opaque credential references.

This does not retrieve keys, probe venues, allocate budgets or persist ownership.
Reconstruction requires verified evidence again. Failed binding raises and stays
UNVERIFIED; it cannot manufacture a VenueAccount from a key string.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import RLock

from trading_system.contracts import types as t
from .authorization import utc_now, valid_evidence


class IdentityVerificationError(ValueError):
    pass


class IdentityCatalog:
    def __init__(self, environment: t.Environment, *,
                 verify: Callable[[str, t.AccountIdentityEvidence], bool],
                 clock: Callable[[], datetime] = utc_now):
        if not isinstance(environment, t.Environment):
            raise ValueError('typed environment required')
        self.environment = environment
        self._verify = verify
        self._clock = clock
        self._accounts: dict[str, t.VenueAccount] = {}
        self._credentials: dict[str, str] = {}
        self._states: dict[str, t.CapabilityStatus] = {}
        self._lock = RLock()

    def status(self, credential_ref: str) -> t.CapabilityStatus:
        with self._lock:
            return self._states.get(credential_ref, t.CapabilityStatus.UNVERIFIED)

    def bind(self, credential_ref: str, evidence: t.AccountIdentityEvidence) -> t.VenueAccount:
        # Verification is an injected local evidence check, not a platform call.
        with self._lock:
            self._states[credential_ref] = t.CapabilityStatus.UNVERIFIED
            if (not credential_ref or not evidence.venue_account_id or not evidence.venue
                    or credential_ref == evidence.venue_account_id
                    or evidence.environment != self.environment
                    or not isinstance(evidence.product, t.Product)
                    or not valid_evidence(evidence.evidence, self.environment, self._clock())):
                raise IdentityVerificationError('invalid or cross-environment identity evidence')
            try:
                verified = self._verify(credential_ref, evidence)
            except Exception as exc:
                raise IdentityVerificationError('identity verifier unavailable') from exc
            if verified is not True:
                raise IdentityVerificationError('identity and permissions are unverified')
            previous = self._credentials.get(credential_ref)
            if previous is not None and previous != evidence.venue_account_id:
                raise IdentityVerificationError('credential cannot be rebound to another account')
            account = self._accounts.get(evidence.venue_account_id)
            if account is not None and account.venue != evidence.venue:
                raise IdentityVerificationError('account ID has a conflicting venue')
            refs = account.credential_refs if account else ()
            products = account.products if account else ()
            ref = t.CredentialRef(id=credential_ref)
            result = t.VenueAccount(
                venue_account_id=evidence.venue_account_id, venue=evidence.venue,
                environment=self.environment,
                products=products if evidence.product in products else products + (evidence.product,),
                credential_refs=refs if ref in refs else refs + (ref,), identity_evidence=evidence)
            self._accounts[result.venue_account_id] = result
            self._credentials[credential_ref] = result.venue_account_id
            self._states[credential_ref] = t.CapabilityStatus.VERIFIED
            return result
