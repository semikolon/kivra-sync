#!/usr/bin/python3
# -*- coding: utf-8 -*-

import requests
import hashlib
import base64
import secrets
import time
import qrcode
import os
import logging
import json
import sys
from typing import Optional

from kivra import tokens as _token_store


class KivraAuth:
    """Class for handling Kivra authentication via BankID."""
    
    def __init__(self, temp_dir, interaction_provider):
        """
        Initialize the Kivra authentication handler.
        
        Args:
            temp_dir (str): Directory for temporary files like QR codes
            interaction_provider (InteractionProvider): Provider for user interaction
        """
        self.temp_dir = temp_dir
        self.interaction_provider = interaction_provider
        self.session = requests.Session()
        self.client_id = "14085255171411300228f14dceae786da5a00285fe"
        
    def authenticate(self, ssn):
        """
        Authenticate with Kivra using BankID.
        
        Args:
            ssn (str): Social security number in format YYYYMMDDXXXX
            
        Returns:
            dict: Authentication tokens and user information
        """
        # Initialize session
        self.session.get("https://app.kivra.com/")
        
        # Generate code verifier and challenge
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        
        # Initialize OAuth2
        auth_data = self._initialize_oauth2(code_challenge)
        
        # Display QR code and wait for authentication
        qr_code = auth_data.get('qr_code')
        next_poll_url = auth_data.get('next_poll_url')
        auth_code = auth_data.get('code')
        
        if not qr_code or not auth_code:
            logging.error("Missing QR code or auth code in response")
            sys.exit("Authentication initialization failed")
        
        # Generate and save QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_code)
        qr.make(fit=True)
        
        # Create and save QR code as a temporary image
        img = qr.make_image(fill_color="black", back_color="white")
        temp_path = os.path.join(self.temp_dir, "kivra_qr.png")
        img.save(temp_path)
        
        # Display QR code using the interaction provider
        self.interaction_provider.display_qr_code(temp_path)
        print("\nQR-kod visas nu. Skanna den med BankID-appen.")

        # Poll for authentication completion. `temp_path` is passed so the
        # poll loop can regenerate the PNG on each pending response — Kivra
        # animates the BankID QR server-side (~1 s rotation), and refreshing
        # the file in-place lets LocalHtmlInteractionProvider's viewer pick up
        # the fresh code via its existing 800 ms cache-bust loop.
        token_info = self._poll_for_auth(
            next_poll_url, auth_code, code_verifier, temp_path
        )
        
        # Clean up temporary QR code file
        try:
            os.remove(temp_path)
        except:
            pass
            
        return token_info
    
    def _generate_code_verifier(self):
        """Generate a code verifier for PKCE."""
        return secrets.token_urlsafe(32)
    
    def _generate_code_challenge(self, code_verifier):
        """Generate a code challenge from the code verifier using SHA-256."""
        code_challenge = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(code_challenge).decode('utf-8').rstrip('=')
        return code_challenge
    
    def _initialize_oauth2(self, code_challenge):
        """
        Initialize OAuth2 authorization with Kivra.
        
        Args:
            code_challenge (str): PKCE code challenge
            
        Returns:
            dict: Authorization data including QR code and polling URL
        """
        auth_url = "https://app.api.kivra.com/v2/oauth2/authorize"
        auth_params = {
            'response_type': 'bankid_all',
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
            'scope': 'openid profile',
            'client_id': self.client_id,
            'redirect_uri': 'https://inbox.kivra.com/auth/kivra/return'
        }
        
        logging.info("Initializing OAuth2 authorization")
        
        r = self.session.post(auth_url, 
                             json=auth_params,
                             headers={
                                 'Content-Type': 'application/json',
                                 'Accept': 'application/json',
                                 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                             })
        
        if r.status_code not in [201, 202]:
            logging.error(f"OAuth2 authorization failed. Status: {r.status_code}, Response: {r.text}")
            sys.exit("Could not initialize OAuth2")
        
        return r.json()
    
    
    def _poll_for_auth(self, next_poll_url, auth_code, code_verifier, temp_path=None):
        """
        Poll for BankID authentication completion.

        Args:
            next_poll_url (str): URL to poll for authentication status
            auth_code (str): Authorization code
            code_verifier (str): PKCE code verifier
            temp_path (str | None): Path to the QR PNG file. If provided AND
                Kivra includes a rotating ``qr_code`` field in pending poll
                responses, the file is regenerated in-place each poll and the
                interaction_provider's ``refresh_qr_code`` is called so the
                self-refreshing viewer picks up the fresh code without
                re-opening the tab. None preserves the legacy single-PNG
                behaviour for any caller that hasn't passed it through.

        Returns:
            dict: Token information including access_token and actor_key
        """
        print("\nWaiting for BankID authentication...")

        # BankID animated-QR rotates ~1 s server-side; 1.5 s polling is the
        # balance between rotation freshness and Kivra-side rate-limit safety
        # (legacy was 5 s — too slow for BankID's ~30 s order TTL when the
        # user is even briefly distracted).
        poll_interval_s = 1.5
        # Whether Kivra's pending responses include rotating qr_code. Logged
        # ONCE on first observation so the field is auditable without spamming.
        rotating_qr_logged = False

        while True:
            time.sleep(poll_interval_s)
            poll_response = self.session.get(f"https://app.api.kivra.com{next_poll_url}")
            poll_data = poll_response.json()

            # Rotating-QR refresh: if Kivra returns a fresh qr_code in this
            # pending response, regenerate the PNG and tell the viewer to
            # reload it. Robust to absence (legacy / non-animated path stays
            # a no-op). Best-effort — viewer failures never derail auth.
            if temp_path and poll_data.get('status') == 'pending':
                fresh_qr = poll_data.get('qr_code')
                if fresh_qr:
                    if not rotating_qr_logged:
                        logging.info(
                            "Kivra pending poll includes qr_code — rotating "
                            "QR refresh enabled (BankID animated-QR contract)"
                        )
                        rotating_qr_logged = True
                    try:
                        qr_new = qrcode.QRCode(
                            version=1,
                            error_correction=qrcode.constants.ERROR_CORRECT_L,
                            box_size=10,
                            border=4,
                        )
                        qr_new.add_data(fresh_qr)
                        qr_new.make(fit=True)
                        qr_new.make_image(
                            fill_color="black", back_color="white"
                        ).save(temp_path)
                        self.interaction_provider.refresh_qr_code(temp_path)
                    except Exception as e:  # noqa: BLE001 - never derail auth
                        logging.warning(
                            "Could not refresh rotating QR (%s); BankID order "
                            "may expire if user is distracted", e
                        )

            if poll_data.get('status') == 'complete':
                print("\nBankID authentication successful!")
                
                # Notify interaction provider that authentication succeeded
                self.interaction_provider.report_authentication_success()
                
                # Exchange authorization code for tokens
                token_url = "https://app.api.kivra.com/v2/oauth2/token"
                token_data = {
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "client_id": self.client_id,
                    "redirect_uri": "https://inbox.kivra.com/auth/kivra/return",
                    "code_verifier": code_verifier
                }
                
                print("Fetching OAuth token...")
                token_response = self.session.post(token_url, 
                                                 json=token_data,
                                                 headers={'Content-Type': 'application/json'})
                
                if token_response.status_code != 200:
                    logging.error(f"Failed to fetch token. Status: {token_response.status_code}, Response: {token_response.text}")
                    sys.exit("Token retrieval failed")
                
                token_info = token_response.json()
                access_token = token_info.get('access_token')
                id_token = token_info.get('id_token')
                
                # Decode JWT to get user information
                id_token_parts = id_token.split('.')
                if len(id_token_parts) < 2:
                    logging.error("Invalid id_token structure")
                    sys.exit("Could not parse id_token")
                
                # Decode base64
                padding = '=' * (4 - len(id_token_parts[1]) % 4)
                jwt_payload = base64.b64decode(id_token_parts[1] + padding)
                jwt_data = json.loads(jwt_payload)
                
                # Get kivra_user_id from JWT
                actor_key = jwt_data.get('kivra_user_id')
                
                if not actor_key:
                    logging.error(f"Could not find kivra_user_id in token: {jwt_data}")
                    sys.exit("Missing kivra_user_id")
                
                # Return token information.
                # `refresh_token` + `expires_in` added in the fork so the orchestration
                # in authenticate_with_refresh_fallback() can persist them. Additive
                # change — existing callers reading only access_token/actor_key/jwt_data
                # are unaffected.
                return {
                    'access_token': access_token,
                    'refresh_token': token_info.get('refresh_token'),
                    'expires_in': token_info.get('expires_in', 3600),
                    'actor_key': actor_key,
                    'jwt_data': jwt_data
                }

            elif poll_data.get('status') == 'pending':
                print(".", end="", flush=True)  # Show progress
            else:
                logging.error(f"Error during polling. Status: {poll_data.get('status')}, Response: {poll_data}")
                sys.exit("BankID authentication failed")

    # ----- Fork additions: refresh-token orchestration (Phase 2 of spec) -----

    def authenticate_with_refresh_fallback(self, ssn: str) -> dict:
        """Orchestrates auth across three paths:
            1. cached access_token still valid → reuse (zero network)
            2. cached refresh_token present → refresh-token grant
            3. BankID QR flow (existing authenticate)

        Always persists tokens after any successful auth (paths 2 + 3 write;
        path 1 reads from the existing persisted state). Returns the same
        shape as authenticate() for caller compatibility.
        """
        cached = _token_store.load_tokens(ssn)

        # Path 1: cached valid access_token (FR-4)
        if cached and not _token_store.is_access_token_expired(cached):
            logging.info("Using cached access_token (no network call to Kivra)")
            return self._auth_dict_from_cached(cached)

        # Path 2: refresh-token grant (FR-1)
        if cached and _token_store.has_refresh_token(cached):
            logging.info("Cached access_token expired; attempting refresh-token grant")
            refresh_response = self._try_refresh(cached["refresh_token"])
            if refresh_response is not None:
                _token_store.save_tokens(
                    ssn,
                    response=refresh_response,
                    jwt_data=cached.get("id_token_jwt_data", {}),
                    prior=cached,
                )
                return self._auth_dict_from_cached(_token_store.load_tokens(ssn))
            # else: fall through to BankID

        # Path 3: BankID QR flow (FR-5)
        logging.info("Falling back to BankID QR flow")
        auth_dict = self.authenticate(ssn)
        _token_store.save_tokens(
            ssn,
            response={
                "access_token": auth_dict["access_token"],
                "refresh_token": auth_dict.get("refresh_token"),
                "expires_in": auth_dict.get("expires_in", 3600),
            },
            jwt_data=auth_dict.get("jwt_data", {}),
        )
        return auth_dict

    def _try_refresh(self, refresh_token: str) -> Optional[dict]:
        """POST /v2/oauth2/token with grant_type=refresh_token.

        Returns response dict on 200 + valid access_token. Returns None on any
        failure mode: 4xx, 5xx, network error, non-JSON response, response
        missing access_token. Failure reasons logged at INFO (recoverable) or
        WARNING (unexpected); never raises.
        """
        token_url = "https://app.api.kivra.com/v2/oauth2/token"
        try:
            r = requests.post(
                token_url,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                },
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
        except requests.exceptions.RequestException as e:
            logging.warning("Refresh request network error: %s; falling back to BankID", e)
            return None

        if r.status_code == 200:
            try:
                data = r.json()
            except (json.JSONDecodeError, ValueError):
                logging.warning("Refresh response 200 but body is not JSON; falling back to BankID")
                return None
            if not data.get("access_token"):
                logging.warning(
                    "Refresh response 200 but missing access_token; falling back to BankID"
                )
                return None
            return data
        elif r.status_code in (400, 401):
            logging.info(
                "Refresh-token grant rejected (HTTP %s); falling back to BankID", r.status_code
            )
            return None
        else:
            logging.warning(
                "Refresh-token grant returned HTTP %s; falling back to BankID", r.status_code
            )
            return None

    def _auth_dict_from_cached(self, cached: dict) -> dict:
        """Translates a stored tokens dict (load_tokens schema) to the auth_dict
        shape that downstream consumers (KivraApiClient) expect.
        """
        return {
            "access_token": cached["access_token"],
            "refresh_token": cached.get("refresh_token"),
            "expires_in": None,  # not meaningful post-load (use is_access_token_expired)
            "actor_key": cached.get("actor_key"),
            "jwt_data": cached.get("id_token_jwt_data", {}),
        }
