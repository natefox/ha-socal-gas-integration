"""Browser authentication via Browserless Chrome /function API.

Sends Puppeteer JavaScript to a Browserless Chrome instance over HTTP.
The JS runs headless Chromium to handle the socalgas.com login flow,
captures the AccessToken from SmartCMobile requests, and returns it.

Once the AccessToken is obtained, all subsequent SmartCMobile API calls
(gnnmapping, Green Button download) work with plain HTTP.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp

from .totp import upcoming_codes

_LOGGER = logging.getLogger(__name__)

# Puppeteer code executed inside Browserless via /function endpoint.
# Receives {page, context} where context = {username, password}.
# Returns JSON with access_token + account_number, or error + error_type.
_LOGIN_JS = r"""
export default async ({ page, context }) => {
  const { username, password, totpCodes } = context;

  let accessToken = null;
  let accountNumber = null;

  // Capture AccessToken from outgoing SmartCMobile requests
  page.on('request', (request) => {
    if (request.url().includes('smartcmobile.com')) {
      const headers = request.headers();
      if (headers['accesstoken']) {
        accessToken = headers['accesstoken'];
      }
    }
  });

  // Capture account number from API responses
  page.on('response', async (response) => {
    if (response.url().includes('get-bill-account-list') && response.status() === 200) {
      try {
        const data = await response.json();
        if (data && typeof data === 'object') {
          const accounts = data.billAccounts || data.accounts || [];
          if (Array.isArray(accounts) && accounts.length > 0) {
            const acct = accounts[0];
            let num = String(acct.billAccountNumber || acct.accountNumber || '');
            if (num.length === 11) num = num.slice(0, 10);
            if (num) accountNumber = num;
          }
        }
      } catch (e) { /* ignore parse errors */ }
    }
  });

  await page.setUserAgent(
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  );

  // Step 1: Login page
  await page.goto('https://myaccount.socalgas.com/ui/login', {
    waitUntil: 'networkidle2',
    timeout: 60000,
  });

  // Step 2: Fill credentials using keyboard input
  // Login form uses web components (scg-text-field) with shadow DOM,
  // so regular selectors can't reach the inputs. We get element handles
  // to the shadow DOM inputs and use keyboard.type() for real key events.
  const emailHandle = await page.evaluateHandle(() => {
    const fields = document.querySelectorAll('scg-text-field');
    for (const host of fields) {
      const sr = host.shadowRoot;
      if (!sr) continue;
      const inp = sr.querySelector('input');
      if (inp && (inp.type === 'email' || inp.id === 'email')) return inp;
    }
    return null;
  });

  const passHandle = await page.evaluateHandle(() => {
    const fields = document.querySelectorAll('scg-text-field');
    for (const host of fields) {
      const sr = host.shadowRoot;
      if (!sr) continue;
      const inp = sr.querySelector('input');
      if (inp && (inp.type === 'password' || inp.id === 'password')) return inp;
    }
    return null;
  });

  const inputsFound = await page.evaluate(
    (e, p) => !!e && !!p, emailHandle, passHandle
  );
  if (!inputsFound) {
    return {
      data: { error: 'Could not find login form fields on page', error_type: 'connection' },
      type: 'application/json',
    };
  }

  // Focus and type into each field — real keyboard events work with
  // the web component framework unlike programmatic value setting.
  await emailHandle.focus();
  await page.keyboard.type(username, { delay: 30 });
  await passHandle.focus();
  await page.keyboard.type(password, { delay: 30 });

  await new Promise(r => setTimeout(r, 500));

  // Find and click the "Log In" button inside scg-button shadow DOM.
  // There are multiple scg-button elements on the page; target by text.
  const clicked = await page.evaluate(() => {
    const scgBtns = document.querySelectorAll('scg-button');
    for (const host of scgBtns) {
      const sr = host.shadowRoot;
      if (!sr) continue;
      const b = sr.querySelector('button');
      if (b && b.textContent.trim() === 'Log In') {
        b.click();
        return true;
      }
    }
    // Fallback: any submit button
    const btn = document.querySelector('button[type="submit"]');
    if (btn) { btn.click(); return true; }
    return false;
  });

  if (!clicked) {
    return {
      data: { error: 'Could not find submit button on login page', error_type: 'connection' },
      type: 'application/json',
    };
  }

  // Step 3: Wait for redirect away from login, answering a 2-step
  // verification prompt on the way. The code
  // prompt stays under /ui/login, so "left /ui/login" alone isn't enough.
  // Everything below runs in one page.evaluate per tick because the form
  // lives in nested shadow roots.
  const scan = () => page.evaluate(() => {
    const all = [];
    const walk = (root) => {
      for (const el of root.querySelectorAll('*')) {
        all.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const describe = (el) => {
      const host = el.getRootNode() && el.getRootNode().host;
      return [el.id, el.name, el.placeholder, el.getAttribute('aria-label'),
              el.getAttribute('autocomplete'), host && host.getAttribute('label'),
              host && host.textContent].filter(Boolean).join(' ');
    };
    const inputs = all.filter((el) => el.tagName === 'INPUT' && visible(el));
    const password = inputs.find((el) => el.type === 'password');
    const otpRe = /one-time-code|\botp\b|code|passcode|verif|token|authenticat|2fa|mfa/i;
    const skip = ['email', 'password', 'hidden', 'checkbox', 'radio', 'submit', 'button'];
    const candidates = inputs.filter((el) => !skip.includes(el.type));
    // One box per digit (six maxlength=1 inputs) or a single code field.
    const boxes = password ? [] : candidates.filter((el) => el.maxLength === 1);
    let otp = boxes.length >= 6 ? boxes[0] : undefined;
    if (!otp) otp = candidates.find((el) => el.autocomplete === 'one-time-code' || otpRe.test(describe(el)));
    if (!otp && !password) {
      otp = candidates.find((el) => el.maxLength === 6 || el.inputMode === 'numeric');
    }
    const clickable = all.filter((el) => (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button'
      || el.getAttribute('role') === 'radio' || el.tagName === 'LABEL') && visible(el));
    const text = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
    // Tag what the next step may click, so the Node side can act on it.
    all.forEach((el) => el.removeAttribute && el.removeAttribute('data-scg-patch'));
    if (boxes.length >= 6) boxes.slice(0, 6).forEach((el, i) => el.setAttribute('data-scg-patch', 'otp-' + i));
    else if (otp) otp.setAttribute('data-scg-patch', 'otp');
    const submit = [/^(verify|submit|confirm)\b/i, /^(continue|next|send)\b/i, /^(log ?in|sign ?in)\b/i]
      .map((re) => clickable.find((el) => re.test(text(el)))).find(Boolean);
    if (submit) submit.setAttribute('data-scg-patch', 'submit');
    const method = clickable.find((el) => /authenticator/i.test(text(el)));
    if (method && !otp && !password) method.setAttribute('data-scg-patch', 'method');
    const bodyText = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
    return {
      url: window.location.href,
      onLogin: window.location.href.includes('/ui/login'),
      password: !!password,
      otp: !!otp,
      otpBoxes: boxes.length >= 6,
      submit: !!submit,
      method: !!(method && !otp && !password),
      text: bodyText.slice(0, 1500),
      inputs: inputs.map((el) => ({ type: el.type, desc: describe(el).slice(0, 80),
                                    maxlength: el.maxLength, inputmode: el.inputMode })),
      buttons: clickable.map(text).filter(Boolean).slice(0, 25),
    };
  });
  // Elements tagged by scan(), found again through the shadow roots.
  const tagged = (tag) => page.evaluateHandle((t) => {
    const find = (root) => {
      for (const el of root.querySelectorAll('*')) {
        if (el.getAttribute('data-scg-patch') === t) return el;
        if (el.shadowRoot) { const f = find(el.shadowRoot); if (f) return f; }
      }
      return null;
    };
    return find(document);
  }, tag);
  const debugOf = (st) => ({ url: st.url, text: st.text, inputs: st.inputs, buttons: st.buttons });

  const deadline = Date.now() + 90000;
  let attempts = 0;
  let methodClicked = false;
  let methodContinued = false;
  let lastSubmit = 0;
  let st = null;
  for (;;) {
    await new Promise(r => setTimeout(r, 1000));
    try { st = await scan(); } catch (e) { continue; }  // navigating
    // Done once off the login page, unless the code prompt has its own URL.
    // (Not "any code field": the account pages have ZIP code inputs.)
    if (!st.onLogin && !/mfa|verif|otp|factor|2fa|challenge/i.test(st.url)) break;
    if (Date.now() > deadline) {
      return {
        data: {
          error: st.password
            ? 'Login failed — still on the login form. Check your username and password.'
            : 'Login failed — stuck after the password step (2-step verification?).',
          error_type: 'auth',
          debug: debugOf(st),
        },
        type: 'application/json',
      };
    }
    if (st.password) continue;
    if (st.method && !methodClicked) {
      methodClicked = true;
      const m = await tagged('method');
      if (m) { await m.click(); }
      continue;
    }
    // A method picker usually needs a Continue/Send after the choice.
    if (methodClicked && !methodContinued && !st.otp && st.submit) {
      methodContinued = true;
      const b = await tagged('submit');
      if (b) { await b.click(); }
      continue;
    }
    if (st.otp && Date.now() - lastSubmit > 12000) {
      if (!totpCodes) {
        return {
          data: {
            error: 'SoCal Gas asked for a 2-step verification code. Add your authenticator setup key (TOTP secret) to the integration.',
            error_type: 'auth',
            debug: debugOf(st),
          },
          type: 'application/json',
        };
      }
      if (attempts >= 2) {
        return {
          data: { error: 'SoCal Gas rejected the verification code twice. Check the TOTP setup key.', error_type: 'auth', debug: debugOf(st) },
          type: 'application/json',
        };
      }
      // Don't start typing a code that expires mid-entry.
      if ((Date.now() / 1000) % 30 > 26) continue;
      const code = totpCodes[String(Math.floor(Date.now() / 1000 / 30))];
      if (!code) {
        return {
          data: { error: 'No verification code for the current time (clock skew?).', error_type: 'connection', debug: debugOf(st) },
          type: 'application/json',
        };
      }
      attempts += 1;
      if (st.otpBoxes) {
        for (let i = 0; i < 6; i++) {
          const b = await tagged('otp-' + i);
          if (!b) break;
          await b.click({ clickCount: 3 });
          await page.keyboard.type(code[i], { delay: 60 });
        }
      } else {
        const box = await tagged('otp');
        if (!box) continue;
        await box.click({ clickCount: 3 });
        await page.keyboard.type(code, { delay: 60 });
      }
      await new Promise(r => setTimeout(r, 500));
      const fresh = await scan();
      const btn = fresh.submit ? await tagged('submit') : null;
      if (btn) { await btn.click(); } else { await page.keyboard.press('Enter'); }
      lastSubmit = Date.now();
    }
  }

  // Let the page settle
  await new Promise(r => setTimeout(r, 3000));

  // Check for error page (rate limiting)
  if (page.url().includes('/ui/error')) {
    return {
      data: {
        error: 'Login redirected to error page — site may be rate-limiting. Try again in a few minutes.',
        error_type: 'connection',
      },
      type: 'application/json',
    };
  }

  // Step 3.5: Detect CCCI interstitial popup
  if (page.url().includes('/ui/interstitials')) {
    return {
      data: {
        error: 'SoCal Gas requires you to confirm account information. Please log in to socalgas.com in a browser, address the popup, then retry.',
        error_type: 'interstitial',
      },
      type: 'application/json',
    };
  }

  // Step 4: Navigate to usage page to trigger SSO + AccessToken
  await page.goto('https://myaccount.socalgas.com/ui/ways-to-save/analyze-usage', {
    waitUntil: 'networkidle2',
    timeout: 60000,
  });

  // Wait for SmartCMobile requests to fire
  await new Promise(r => setTimeout(r, 3000));

  if (!accessToken) {
    await new Promise(r => setTimeout(r, 5000));
  }

  if (!accessToken) {
    return {
      data: {
        error: 'Could not capture AccessToken from browser session. The usage page may not have loaded the energy widget.',
        error_type: 'auth',
      },
      type: 'application/json',
    };
  }

  return {
    data: { access_token: accessToken, account_number: accountNumber || '' },
    type: 'application/json',
  };
};
"""


def _build_function_url(base_url: str, timeout_ms: int = 120000) -> str:
    """Build the /function endpoint URL from the Browserless base URL.

    Handles base URLs with or without query params (e.g. token).
    Adds a timeout parameter so Browserless doesn't kill the session early.
    http://browserless:3000?token=X -> http://browserless:3000/function?token=X&timeout=120000
    """
    parsed = urlparse(base_url)
    new_path = parsed.path.rstrip("/") + "/function"
    # Merge existing query params with timeout
    params = parse_qs(parsed.query)
    params["timeout"] = [str(timeout_ms)]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(path=new_path, query=new_query))


async def browser_authenticate(
    browserless_url: str, username: str, password: str,
    totp_secret: str | None = None,
) -> tuple[str, str]:
    """Authenticate via Browserless Chrome /function API.

    Args:
        browserless_url: Base URL of Browserless (e.g. http://browserless:3000).
        username: SoCal Gas username/email.
        password: SoCal Gas password.
        totp_secret: Authenticator setup key, for accounts with 2-step
            verification.

    Returns:
        Tuple of (access_token, account_number).

    Raises:
        SoCalGasAuthError: If login credentials are wrong.
        SoCalGasConnectionError: If Browserless is unreachable or browser fails.
    """
    from .api import SoCalGasAuthError, SoCalGasConnectionError

    function_url = _build_function_url(browserless_url, timeout_ms=180000)
    context = {"username": username, "password": password}
    if totp_secret:
        context["totpCodes"] = upcoming_codes(totp_secret)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                function_url,
                json={
                    "code": _LOGIN_JS,
                    "context": context,
                },
                timeout=aiohttp.ClientTimeout(total=190),
            ) as resp:
                if resp.status == 401:
                    raise SoCalGasConnectionError(
                        "Browserless authentication failed. "
                        "If your Browserless instance requires a token, "
                        "append ?token=YOUR_TOKEN to the Browserless URL."
                    )
                if resp.status != 200:
                    text = await resp.text()
                    raise SoCalGasConnectionError(
                        f"Browserless error ({resp.status}): {text[:200]}"
                    )
                raw = await resp.json()
                # Browserless wraps the return in {"data": ..., "type": ...}
                data = raw.get("data", raw) if isinstance(raw, dict) else raw

    except SoCalGasConnectionError:
        raise
    except aiohttp.ClientError as err:
        raise SoCalGasConnectionError(
            f"Cannot connect to Browserless at {browserless_url}: {err}"
        ) from err
    except Exception as err:
        raise SoCalGasConnectionError(
            f"Browserless request failed: {err}"
        ) from err

    # Check for error responses from our JS code
    if "error" in data:
        if data.get("debug"):
            # What the page showed, so the login steps can be fixed from the
            # log. The password is never on the page; mask the email.
            _LOGGER.warning(
                "SoCal Gas login page at failure: %s",
                json.dumps(data["debug"]).replace(username, "<username>"),
            )
        error_msg = data["error"]
        error_type = data.get("error_type", "connection")
        if error_type in ("auth", "interstitial"):
            raise SoCalGasAuthError(error_msg)
        raise SoCalGasConnectionError(error_msg)

    access_token = data.get("access_token")
    if not access_token:
        raise SoCalGasAuthError(
            "Could not capture AccessToken from browser session."
        )

    account_number = data.get("account_number", "")
    if not account_number:
        _LOGGER.warning(
            "Could not capture account number from browser; "
            "it will need to be obtained via API"
        )

    return access_token, account_number
