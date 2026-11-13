"""Playwright implementation of the reviewed one-application driver.

Own a fresh browser context; never import the user's browser cookies or accounts.
All network traffic goes through the exact reviewed boundary. Raw answers, request
bodies, response bodies, OAuth content and CV data are never logged or persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from time import monotonic
from urllib.parse import parse_qsl, urlsplit

from ejs.services.oneclick_target import OneClickNetworkBoundary, native_entry_policy_enabled
from ejs.services.single_application_runtime import ConfirmationObservation, UploadObservation, digest, require

_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')
_COMPOSED_CONTAINS = """(root, child)=>{
 for(let node=child;node;node=node.parentElement||node.getRootNode().host)if(node===root)return true;
 return false;
}"""


# Static observer code, never supplied by a model, employer or manifest. Traverse
# open shadow roots; fields in opaque frames/closed components remain a blocker.
_DOM_HELPERS = r"""
 const labelOf = node => {
   const root=node.getRootNode();
   const ids=(node.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
   return (node.getAttribute('aria-label') ||
     (node.labels ? Array.from(node.labels).map(x=>x.textContent).join(' ') : '') ||
     ids.map(id=>root.getElementById?.(id)?.textContent||'').join(' ') ||
     node.getAttribute('placeholder') ||
     (node.getAttribute('role')==='radio'?node.textContent:'') || '').replace(/\s+/g,' ').trim();
 };
 const descendants = node => {
   const out=[];
   const walk = root => {for(const child of root.children||[]){out.push(child);walk(child);if(child.shadowRoot)walk(child.shadowRoot);}};
   walk(node);if(node.shadowRoot)walk(node.shadowRoot);return out;
 };
 const radiosOf = node => descendants(node).filter(child =>
   child.type==='radio'||(child.getAttribute('role')==='radio'&&!descendants(child).some(x=>x.type==='radio')));
"""
_DESCRIBE = "el => {" + _DOM_HELPERS + r"""
 const role=el.getAttribute('role');
 const kind=el.tagName==='SELECT'||role==='combobox'?'select':role==='radiogroup'?'radio':el.tagName==='TEXTAREA'?'text':
   el.tagName==='INPUT'?el.type:({combobox:'select',textbox:'text'}[role]||role||'unknown');
 const radios=role==='radiogroup'?radiosOf(el):[];
 return {tag:el.tagName.toLowerCase(),kind,label:labelOf(el),name:el.getAttribute('name')||'',
   required:el.required===true||el.hasAttribute('required')||el.getAttribute('aria-required')==='true'||radios.some(x=>x.required===true||x.getAttribute('aria-required')==='true'),
   disabled:el.disabled===true||el.getAttribute('aria-disabled')==='true',
   options:el.tagName==='SELECT'?Array.from(el.options).map(x=>x.textContent.trim()):radios.map(labelOf)};
}"""
_DEDUPLICATED_SPL_WRAPPER = "el => {" + _DOM_HELPERS + r"""
 const tag=el.tagName;
 if(!['SPL-NUMBER-FIELD','SPL-INPUT','SPL-AUTOCOMPLETE','SPL-CHECKBOX','SPL-RADIO-GROUP'].includes(tag)||!el.id)return false;
 const semantic=descendants(el).filter(node => {
   if(node.tagName==='INPUT')return !['hidden','button','submit','reset'].includes(node.type);
   return ['SELECT','TEXTAREA'].includes(node.tagName)||['radio','radiogroup','checkbox','combobox','textbox'].includes(node.getAttribute('role'));
 });
 let owned=semantic;
 if(tag==='SPL-RADIO-GROUP'){
   // The reviewed SPL group owns one anonymous FIELDSET, whose radio options
   // are separate SPL shadow components. No other semantic control is hidden.
   owned=semantic.filter(node=>node.type!=='radio'&&node.getAttribute('role')!=='radio');
 }
 if(owned.length!==1)return false;
 const control=owned[0];
 if(!(control.required===true||control.getAttribute('aria-required')==='true'))return false;
 if(tag==='SPL-RADIO-GROUP')return control.tagName==='FIELDSET'&&control.getAttribute('role')==='radiogroup'&&(!control.id||control.id===el.id);
 if(control.tagName!=='INPUT'||control.id!==el.id)return false;
 if(tag==='SPL-NUMBER-FIELD')return control.type==='number';
 if(tag==='SPL-AUTOCOMPLETE')return control.type==='text'&&control.getAttribute('role')==='combobox';
 if(tag==='SPL-CHECKBOX')return control.type==='checkbox';
 return ['text','email','tel','number','search'].includes(control.type);
}"""
_VALUE = "el => {" + _DOM_HELPERS + """
 if(el.getAttribute('role')==='radiogroup'){
   const checked=radiosOf(el).filter(x=>x.type==='radio'?x.checked:x.getAttribute('aria-checked')==='true');
   return checked.length===1?labelOf(checked[0]):checked.length?'AMBIGUOUS_RADIO_SELECTION':'';
 }
 if(el.getAttribute('role')==='combobox'){
   const ariaValue=el.getAttribute('aria-valuetext');
   return ariaValue!==null?ariaValue:(typeof el.value==='string'?el.value:'');
 }
 if(el.type==='checkbox'||el.type==='radio')return el.checked;
 if(['radio','checkbox'].includes(el.getAttribute('role')))return el.getAttribute('aria-checked')==='true';
 if(el.tagName==='SELECT')return el.selectedOptions?.[0]?.textContent.trim()||'';
 return el.value||'';
}"""
_RETURN_DOM = "el => {" + _DOM_HELPERS + """
 return [el,...descendants(el)].map(node=>({tag:node.tagName,shadow:!!node.shadowRoot,
   children:node.children.length,attributes:Array.from(node.attributes).filter(x=>
     ['id','name','role','type','class','style','hidden','required','disabled','checked','selected',
      'autocomplete','for','tabindex','src','href','action','formaction'].includes(x.name)||x.name.startsWith('aria-')
   ).map(x=>[x.name,x.value]).sort()}));
}"""


def _widget_url(url):
    try:
        require(type(url) is str and len(url) <= 4096 and url.isascii()
                and not any(ord(c) < 33 or ord(c) == 127 for c in url) and '\\' not in url)
        parsed = urlsplit(url)
        require(parsed.scheme == 'https' and parsed.netloc in ('linkedin.com', 'www.linkedin.com')
                and parsed.path == '/talentwidgets/apply-with-linkedin' and not parsed.query)
        # Actual reviewed widget metadata uses these two public fragment keys,
        # not a query/redirect/OAuth permission URL. No other keys are accepted.
        pairs = parse_qsl(parsed.fragment, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        require(len(pairs) == len({key for key, _ in pairs})
                and all(re.fullmatch(r'[A-Za-z0-9_]{1,64}', key) and len(value) <= 2048
                        and not any(ord(c) < 32 or ord(c) == 127 for c in value) for key, value in pairs))
        values = dict(pairs)
        require(set(values) == {'xdOrigin', 'xdChannel'}
                and values['xdOrigin'] == 'https://jobs.smartrecruiters.com'
                and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', values['xdChannel']))
        return True
    except Exception:
        return False


class OneClickBrowser:
    def __init__(self, context, boundary: OneClickNetworkBoundary, manifest: dict):
        self.context, self.boundary, self.manifest = context, boundary, manifest
        self.page = context.new_page()
        self.page.set_default_timeout(10_000)
        self._step = 0
        self._external_surface = False
        self._submit_called = False
        self._upload = None
        self._popup = False
        self._response = None
        self._owned_page, self._owned_context, self._owned_boundary = self.page, context, boundary
        self._owned_target = boundary.target
        self._entry_handoff_pending = self._entry_observation_issued = self._returned_entry_reviewed = False
        self._entry_return_ready = self._entry_return_consumed = self._returned_widget_allowed = False
        self._entry_observation = self._accepted_entry_observation = self._handoff_widget = None
        self._handoff_widget_src = None
        self._entry_effect_attempts = self._popup_generation = self._document_generation = 0
        self._entry_network_fault = False
        native_entry_policy_enabled(manifest)
        self._entry_policy_pin = digest(manifest.get('entry_policy'))
        self._native_entry_selected = False
        self._native_widgets = []
        self._profile_correction_started = self._profile_corrections_applied = False
        context.route('**/*', self._route)
        # Playwright >=1.48 is required for this lane (the legacy project minimum
        # remains unchanged). Do not silently continue if WebSocket fencing is absent.
        require(callable(getattr(context, 'route_web_socket', None)), 'APPLICATION_WEBSOCKET_FENCE_REQUIRED')
        # A routed socket stays disconnected unless connect_to_server() is
        # explicitly called. Synchronous close here can deadlock Chromium.
        context.route_web_socket('**/*', self._deny_socket)
        context.on('page', self._new_page)
        self.page.on('response', self._observe_response)
        self.page.on('framenavigated', self._entry_navigation)

    def _entry_navigation(self, frame):
        if frame == self._owned_page.main_frame:
            self._document_generation += 1

    def _new_page(self, page):
        if page != self.page:
            self._popup = True
            self._popup_generation += 1
            # A new OAuth/CAPTCHA/permission window is a human handoff. It is
            # never inspected, granted permissions or populated by this driver.

    def _deny_socket(self, socket):
        self._external_surface = True

    def _route(self, route):
        request = route.request
        if self.boundary.allows(request.method, request.url, request.resource_type, request.is_navigation_request()):
            route.fallback()
        else:
            self._entry_network_fault = True
            route.abort('blockedbyclient')

    def _observe_response(self, response):
        if response.request.method == 'POST' and response.url == self.boundary.target.application_url:
            self._response = response

    def open(self, url):
        require(url == self.boundary.target.entry_url)
        self.page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        self.page.locator('input, select, textarea, [role="radio"], [role="radiogroup"], '
                          '[role="combobox"], [role="checkbox"], [role="textbox"]').first.wait_for(state='attached', timeout=30_000)

    def preferred_entry(self):
        require(digest(self.manifest.get('entry_policy')) == self._entry_policy_pin, 'APPLICATION_ENTRY_POLICY_CHANGED')
        if native_entry_policy_enabled(self.manifest):
            require(not self._entry_handoff_pending and not self._native_entry_selected
                    and not self._entry_return_ready and not self._entry_return_consumed
                    and self._entry_effect_attempts == 0, 'APPLICATION_NATIVE_ENTRY_REJECTED')
            self._profile_owned_surface()
            widgets = self.page.locator('iframe').all()
            require(len(widgets) <= 1, 'APPLICATION_NATIVE_ENTRY_REJECTED')
            for widget in widgets:
                src = widget.get_attribute('src')
                require(widget.is_visible() and widget.get_attribute('srcdoc') is None and _widget_url(src)
                        and src.split('#')[0] in self.boundary.native_widget_read_urls, 'APPLICATION_NATIVE_ENTRY_REJECTED')
                frame = widget.element_handle().content_frame()
                require(frame is not None, 'APPLICATION_NATIVE_ENTRY_REJECTED')
                frame.wait_for_url(src, wait_until='domcontentloaded', timeout=3000)
                self._profile_owned_surface()
                self._native_widgets.append((widget.element_handle(), src))
            self._native_entry_selected = True
            return
        if self._entry_return_ready:
            self._entry_return_ready = False
            self._entry_return_consumed = True
            observation = self._accepted_entry_observation
            require(0 <= monotonic() - observation['observed_at'] <= 120, 'APPLICATION_ENTRY_RETURN_REJECTED')
            require(self._entry_snapshot()['proof'] == observation['proof'], 'APPLICATION_ENTRY_RETURN_REJECTED')
            return
        require(not self._entry_return_consumed, 'APPLICATION_ENTRY_RETURN_CONSUMED')
        # Exact visible action only, never an Easy Apply/profile/share link.
        action = self.page.get_by_role('button', name=re.compile(r'^Apply with LinkedIn$', re.I))
        link = self.page.get_by_role('link', name=re.compile(r'^Apply with LinkedIn$', re.I))
        visible = self._visible(action) + self._visible(link)
        widgets = []
        for iframe in self.page.locator('iframe').all():
            require(iframe.is_visible() and iframe.get_attribute('srcdoc') is None
                    and _widget_url(iframe.get_attribute('src')), 'APPLICATION_ENTRY_SURFACE_REVIEW_REQUIRED')
            widgets.append(iframe)
        visible += widgets
        require(len(visible) <= 1, 'APPLICATION_ENTRY_AMBIGUOUS')
        if visible:
            # The first MVP's reviewed network lane deliberately has no OAuth
            # mutation authority. Return to a separately reviewed retained entry
            # after the actual human grant; no blind native retry is permitted.
            self._entry_handoff_pending = True
            if widgets:
                self._handoff_widget = widgets[0].element_handle()
                self._handoff_widget_src = widgets[0].get_attribute('src')
            raise PermissionError('APPLICATION_LINKEDIN_HUMAN_HANDOFF_REQUIRED')

    def _returned_frames_clear(self):
        for iframe in self.page.locator('iframe').all():
            if (not self._returned_widget_allowed or self._handoff_widget is None or not iframe.is_visible()
                    or iframe.get_attribute('srcdoc') is not None or iframe.get_attribute('src') != self._handoff_widget_src
                    or not _widget_url(iframe.get_attribute('src'))
                    or not iframe.evaluate('(el, owned) => el===owned', self._handoff_widget)):
                return False
            frame = iframe.element_handle().content_frame()
            if frame is None or frame.url != self._handoff_widget_src:
                return False
        return True

    def _native_frames_clear(self):
        frames = self.page.locator('iframe').all()
        if not frames:
            return True
        if len(frames) != len(self._native_widgets):
            return False
        for iframe, (owned, src) in zip(frames, self._native_widgets):
            if (not iframe.is_visible() or iframe.get_attribute('srcdoc') is not None
                    or iframe.get_attribute('src') != src or not _widget_url(src)
                    or not iframe.evaluate('(el, owned) => el===owned', owned)):
                return False
            frame = iframe.element_handle().content_frame()
            if frame is None or frame.url != src:
                return False
        return True

    def _entry_snapshot(self):
        require(self.page is self._owned_page and self.context is self._owned_context
                and self.boundary is self._owned_boundary and self.boundary.target is self._owned_target
                and self.page.context is self.context
                and not self.page.is_closed() and self.page.url == self.boundary.target.entry_url
                and self._step == 0 and self._entry_effect_attempts == 0 and not self._submit_called
                and self._upload is None and self.boundary.phase == 'read'
                and self.boundary.application_attempts == self.boundary.application_requests_allowed == self.boundary.upload_requests_allowed == 0
                and self.boundary.blocked_write_count == 0 and not self._entry_network_fault and not self._external_surface
                and self.context.pages == [self.page], 'APPLICATION_ENTRY_RETURN_REJECTED')
        # Check auth/challenge signals before reading or hashing field values.
        body = self.page.locator('body').inner_text()
        require(not re.search(r'verification code|one.time.code|passcode|security code|captcha|verify you are human|'
                              r'access denied|sign in to continue|log in to continue|two.factor|authentication|verify your email',
                              body, re.I), 'APPLICATION_ENTRY_RETURN_REJECTED')
        controls = self._controls()
        required_known = []
        step = self.manifest['steps'][0]
        for binding in step['fields'] + ([step['upload']] if 'upload' in step else []):
            required_known.append(self._locator(binding))
        for locator, info in controls:
            signals = ' '.join(str(locator.get_attribute(attr) or '') for attr in ('type', 'autocomplete', 'name', 'id')) + ' ' + info['label']
            require(info['kind'] in ('text', 'email', 'tel', 'number', 'select', 'checkbox', 'radio', 'file')
                    and not re.search(r'password|passcode|one.time.code|\botp\b|\bpin\b|security.code|verification.code|email.code|sms.code|captcha', signals, re.I)
                    and info['label'].casefold().strip() not in ('code', 'pin', 'otp')
                    and (not info['required'] or any(locator.evaluate('(el, known) => el===known', known.element_handle())
                                                    for known in required_known)), 'APPLICATION_ENTRY_RETURN_REJECTED')
        frames = self.page.locator('iframe').all()
        if frames:
            require(len(frames) == 1 and self._handoff_widget is not None, 'APPLICATION_ENTRY_RETURN_REJECTED')
            iframe = frames[0]
            require(iframe.is_visible() and iframe.get_attribute('srcdoc') is None
                    and iframe.get_attribute('src') == self._handoff_widget_src and _widget_url(self._handoff_widget_src)
                    and iframe.evaluate('(el, owned) => el===owned', self._handoff_widget), 'APPLICATION_ENTRY_RETURN_REJECTED')
            frame = iframe.element_handle().content_frame()
            require(frame is not None and frame.url == self._handoff_widget_src, 'APPLICATION_ENTRY_RETURN_REJECTED')
        shape = sorted([info for _, info in controls], key=lambda x: json.dumps(x, sort_keys=True))
        proof = digest({'dom': self.page.locator('body').evaluate(_RETURN_DOM),
                        'body': hashlib.sha256(body.encode()).hexdigest(),
                        'controls': [(info, locator.evaluate(_VALUE)) for locator, info in controls],
                        'document': self._document_generation, 'popups': self._popup_generation})
        return {'proof': proof, 'surface_fingerprint': digest(shape), 'widget_present': bool(frames)}

    def observe_entry_return(self):
        """Observe only the retained owned entry; never export values or credentials."""
        require(self._entry_handoff_pending and not self._entry_observation_issued, 'APPLICATION_ENTRY_RETURN_REJECTED')
        snapshot = self._entry_snapshot()
        observation = {**snapshot, 'observation_id': secrets.token_hex(24),
                       'observed_at': monotonic(), 'observed_utc': datetime.now(timezone.utc)}
        self._entry_observation, self._entry_observation_issued = observation, True
        return {'observation_id': observation['observation_id'],
                'surface_fingerprint': observation['surface_fingerprint'],
                'observed_at': observation['observed_utc'].isoformat()}

    def accept_reviewed_entry_return(self, observation_id, manifest):
        """Consume once after the caller loads externally pinned fresh facts/review.

        The independent reviewer must inspect the actual values/profile before
        externally pinning this nonce-bound manifest; a structural fingerprint
        is not candidate verification. This API does not perform human OAuth,
        verify the external pin itself or grant network/credential permissions.
        """
        observation, self._entry_observation = self._entry_observation, None
        require(observation is not None and self._entry_handoff_pending, 'APPLICATION_ENTRY_RETURN_REJECTED')
        try:
            require(type(observation_id) is str and secrets.compare_digest(observation_id, observation['observation_id'])
                    and 0 <= monotonic() - observation['observed_at'] <= 120)
            review = manifest['entry_return_review']
            require(type(review) is dict and set(review) == {'observation_id', 'surface_fingerprint', 'reviewed_at', 'reviewer_ref', 'return_url'}
                    and review['observation_id'] == observation_id
                    and review['surface_fingerprint'] == observation['surface_fingerprint']
                    and review['return_url'] == self.boundary.target.entry_url
                    and review['reviewer_ref'] == manifest['reviewer_ref']
                    and type(review['reviewer_ref']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', review['reviewer_ref']))
            reviewed = datetime.fromisoformat(review['reviewed_at'].replace('Z', '+00:00'))
            require(reviewed.tzinfo is not None and reviewed.utcoffset().total_seconds() == 0
                    and observation['observed_utc'] <= reviewed <= datetime.now(timezone.utc)
                    and (datetime.now(timezone.utc) - reviewed).total_seconds() <= 120)
            current = self._entry_snapshot()
            require(current['proof'] == observation['proof']
                    and manifest['steps'][0]['url'] == self.boundary.target.entry_url
                    and manifest['steps'][0]['fingerprint'] == observation['surface_fingerprint'])
            old_manifest = self.manifest
            self.manifest = manifest
            self._returned_widget_allowed = observation['widget_present']
            self._returned_entry_reviewed = True
            historical_popup = self._popup
            self._popup = False  # Only now, after observing all extra pages closed.
            try:
                actual = self.inspect()
                expected = {binding['key'] for binding in manifest['steps'][0]['fields'] if binding['required']}
                if 'upload' in manifest['steps'][0]:
                    expected.add(manifest['steps'][0]['upload']['key'])
                require(actual['fingerprint'] == observation['surface_fingerprint'] and actual['challenge'] is False
                        and actual['unexpected_write'] is False and actual['unreviewed_prefill'] is False
                        and actual['required_keys'] == expected)
            except Exception:
                self.manifest, self._popup = old_manifest, historical_popup
                self._returned_entry_reviewed = self._returned_widget_allowed = False
                raise
            self._accepted_entry_observation = observation
            self._entry_return_ready = True
        except Exception:
            raise PermissionError('APPLICATION_ENTRY_RETURN_REJECTED') from None

    def _locator(self, binding, root=None):
        locator = (root or self.page).locator(binding['selector'])
        require(locator.count() == 1 and (binding.get('kind') == 'file' or locator.is_visible()), 'APPLICATION_CONTROL_CHANGED')
        return locator

    def _controls(self, root=None):
        found = []
        for locator in (root or self.page).locator('input,select,textarea,[role="radio"],[role="radiogroup"],[role="checkbox"],'
                                         '[role="combobox"],[role="textbox"],[aria-required="true"],[required]').all():
            info = locator.evaluate(_DESCRIBE)
            # Only these reviewed SPL hosts may represent the same logical
            # required control as one exact owned native/semantic descendant.
            # Unknown, mismatched or ambiguous wrappers remain blockers.
            if locator.evaluate(_DEDUPLICATED_SPL_WRAPPER):
                continue
            if info['kind'] in ('hidden', 'submit', 'button', 'reset'):
                continue
            if info['kind'] != 'file' and not locator.is_visible():
                # display:contents hosts have no box even when their logical
                # required descendant is visible. An unreviewed wrapper must
                # not disappear merely because its host has no own box.
                owned_visible = (info['required'] and info['kind'] == 'unknown'
                    and '-' in info['tag'] and any(child.is_visible() for child in locator.locator(
                        'input,select,textarea,[role="radio"],[role="radiogroup"],[role="checkbox"],'
                        '[role="combobox"],[role="textbox"]').all()))
                if not owned_visible:
                    continue
            # A reviewed group owns its radio options, including options inside
            # open shadow roots. Other required controls remain independent.
            if locator.evaluate("""el => {
              if(el.type!=='radio'&&el.getAttribute('role')!=='radio')return false;
              for(let node=el.parentElement||el.getRootNode().host;node;node=node.parentElement||node.getRootNode().host){
                if(node.getAttribute('role')==='radiogroup')return true;
              }return false;
            }"""):
                continue
            # Do not count a custom radio wrapper twice if it owns a native input.
            if (info['tag'] != 'input' and info['kind'] == 'radio'
                    and locator.get_attribute('role') != 'radiogroup'
                    and locator.locator('input[type="radio"]').count()):
                continue
            found.append((locator, info))
        return found

    def _profile_owned_surface(self):
        require(self.page is self._owned_page and self.context is self._owned_context
                and self.page.context is self.context and self.boundary is self._owned_boundary
                and self.boundary.target is self._owned_target and not self.page.is_closed()
                and self.page.url == self._owned_target.entry_url and self.context.pages == [self.page]
                and self._step == 0 and self._upload is None and not self._submit_called and not self._popup
                and self.boundary.phase == 'read' and self.boundary.blocked_write_count == 0
                and self.boundary.application_attempts == self.boundary.upload_requests_allowed == 0
                and not self._entry_network_fault and not self._external_surface,
                'APPLICATION_PROFILE_SURFACE_BLOCKED')
        require(digest(self.manifest.get('entry_policy')) == self._entry_policy_pin, 'APPLICATION_ENTRY_POLICY_CHANGED')

    def _profile_guard(self):
        self._profile_owned_surface()
        clear = (self._native_frames_clear() if self._native_entry_selected else
                 self._returned_frames_clear() if self._returned_entry_reviewed else
                 not self.page.locator('iframe').count())
        require(clear and not any(x.is_visible() for x in self.page.locator(
            'input[type="password"],input[autocomplete="one-time-code"]').all()), 'APPLICATION_PROFILE_SURFACE_BLOCKED')
        body = self.page.locator('body').inner_text().casefold()
        require(not any(x in body for x in ('verify you are human', 'complete the captcha', 'verification code', 'access denied')),
                'APPLICATION_PROFILE_SURFACE_BLOCKED')

    def _profile_inventory(self, phase, expected_texts, editing=None):
        root = self.page.locator(phase['container_selector'])
        require(root.count() == 1 and root.is_visible(), 'APPLICATION_PROFILE_IDENTITY_CHANGED')
        observed = root.locator(phase['card_selector']).all()
        require(len(observed) == len(phase['cards']), 'APPLICATION_PROFILE_IDENTITY_CHANGED')
        cards = {}
        for card in phase['cards']:
            locator = root.locator(card['selector'])
            require(locator.count() == 1 and locator.is_visible()
                    and sum(locator.evaluate('(el, other)=>el===other', x.element_handle()) for x in observed) == 1,
                    'APPLICATION_PROFILE_IDENTITY_CHANGED')
            if card['selector'] != editing:
                require(locator.inner_text().strip() == expected_texts[card['selector']], 'APPLICATION_PROFILE_IDENTITY_CHANGED')
            cards[card['selector']] = locator
        require(len({x['selector'] for x in phase['cards']}) == len(observed), 'APPLICATION_PROFILE_IDENTITY_CHANGED')
        return cards

    def _pending_profile_cards(self, step):
        phase = step.get('reviewed_profile_corrections')
        if not phase:
            return {}
        require(not self._profile_correction_started or self._profile_corrections_applied, 'APPLICATION_PROFILE_CORRECTION_FAILED')
        assertions = {a['selector']: a['value'] for a in step['profile_assertions']}
        if self._profile_corrections_applied:
            self._profile_inventory(phase, assertions)
            return {}
        self._profile_guard()
        observed = {c['selector']: c['observed_text'] for c in phase['cards']}
        self._profile_inventory(phase, observed)
        edited = {edit['card_selector'] for edit in phase['edits']}
        # Only a fully identified, explicitly reviewed pending card can defer
        # its final summary. All other profile and ordinary field checks remain.
        return {selector: observed[selector] for selector in edited}

    def _uncovered_profile_actions(self, step):
        if not native_entry_policy_enabled(self.manifest) and 'reviewed_profile_corrections' not in step:
            return False
        phase = step.get('reviewed_profile_corrections')
        for action in self._visible(self.page.get_by_role('button', name=re.compile(r'^Edit (?:experience|education)(?: |$)'))):
            if not phase:
                return True
            owners = []
            for card in phase['cards']:
                locator = self.page.locator(card['selector'])
                if locator.count() == 1 and locator.evaluate("""(el, action)=>{
                  for(let node=action;node;node=node.parentElement||node.getRootNode().host)if(node===el)return true;
                  return false;
                }""", action.element_handle()):
                    owners.append(card)
            if len(owners) != 1:
                return True
        return False

    @staticmethod
    def _profile_button(root, binding):
        action = root.locator(binding['selector'])
        require(action.count() == 1 and action.is_visible() and action.is_enabled()
                and action.evaluate("el=>el.tagName==='BUTTON'")
                and (action.get_attribute('aria-label') or action.inner_text().strip()) == binding['label'],
                'APPLICATION_PROFILE_ACTION_CHANGED')
        return action

    def _profile_editor_surface(self, plan):
        self._profile_guard()
        editor = self.page.locator(plan['selector'])
        require(editor.count() == 1 and editor.is_visible(), 'APPLICATION_PROFILE_EDITOR_CHANGED')
        return self._profile_editor_surface_in(editor, plan)

    def _profile_widget_context(self, binding, editor):
        """Only independently reviewed, owned SPL profile widgets are logical fields."""
        locator = self._locator(binding, editor)
        info = locator.evaluate(_DESCRIBE)
        wrappers, clear, calendar = [], [], None
        if 'month_picker' in binding:
            spec = binding['month_picker']
            field = editor.locator(spec['field_selector'])
            require(field.count() == 1 and field.evaluate("el=>el.tagName==='SPL-DATE-FIELD'&&el.getAttribute('type')==='month-year'")
                    and bool(field.get_attribute('id')), 'APPLICATION_PROFILE_MONTH_WIDGET_CHANGED')
            picker = field.locator(spec['picker_selector'])
            require(picker.count() == 1 and picker.evaluate("el=>el.tagName==='SPL-DATE-PICKER'&&el.getAttribute('type')==='month-year'")
                    and picker.get_attribute('aria-labelledby') == field.get_attribute('id') + '-label'
                    and picker.evaluate(_COMPOSED_CONTAINS, locator.element_handle()), 'APPLICATION_PROFILE_MONTH_WIDGET_CHANGED')
            require(locator.evaluate("el=>el.tagName==='INPUT'&&el.type==='text'&&el.classList.contains('flatpickr-input')")
                    and locator.get_attribute('placeholder') == 'Pick a date' and not locator.get_attribute('role'),
                    'APPLICATION_PROFILE_MONTH_WIDGET_CHANGED')
            calendar = picker.locator(spec['calendar_selector'])
            require(calendar.count() <= 1, 'APPLICATION_PROFILE_MONTH_WIDGET_CHANGED')
            if calendar.count():
                require(calendar.evaluate('el=>el.getAttribute("role")==="dialog"')
                        and calendar.get_attribute('aria-label') == binding['label']
                        and calendar.get_attribute('id') == locator.get_attribute('aria-controls'), 'APPLICATION_PROFILE_MONTH_WIDGET_CHANGED')
            else:
                calendar = None
            wrappers = [field, picker]
            if 'clear' in spec:
                action = picker.locator(spec['clear']['selector'])
                require(action.count() <= 1, 'APPLICATION_PROFILE_MONTH_WIDGET_CHANGED')
                if action.count() and action.is_visible():
                    require((action.get_attribute('aria-label') or action.inner_text().strip()) == spec['clear']['label']
                            and (action.get_attribute('role') == 'button' or action.evaluate("el=>el.tagName==='BUTTON'")),
                            'APPLICATION_PROFILE_MONTH_WIDGET_CHANGED')
                    clear = [action]
            info = {**info, 'kind': 'month', 'widget_type': 'spl-month-year-v1'}
        elif 'text_autocomplete' in binding:
            expected = {'Title': 'OC-JOB-TITLE-AUTOCOMPLETE', 'Company': 'OC-COMPANY-AUTOCOMPLETE', 'City': 'OC-LOCATION-AUTOCOMPLETE',
                        'Office location': 'OC-LOCATION-AUTOCOMPLETE'}[binding['label']]
            host = editor.locator(binding['text_autocomplete']['host_selector'])
            require(host.count() == 1 and host.evaluate('(el, tag)=>el.tagName===tag||el.tagName===tag+"-WRAPPER"', expected)
                    and locator.evaluate("el=>el.tagName==='INPUT'&&el.type==='text'&&el.getAttribute('role')==='combobox'&&!el.readOnly"),
                    'APPLICATION_PROFILE_AUTOCOMPLETE_CHANGED')
            chain = locator.evaluate("""(el, host)=>{
              const out=[];for(let node=el;node;node=node.parentElement||node.getRootNode().host){
                out.push({tag:node.tagName,id:node.id||''});if(node===host)return out;
              }return [];
            }""", host.element_handle())
            tags = [node['tag'] for node in chain]
            require(all(tags.count(tag) == 1 for tag in ('SPL-INPUT', 'SPL-DROPDOWN', 'SPL-AUTOCOMPLETE', expected))
                    and tags.index('SPL-INPUT') < tags.index('SPL-DROPDOWN') < tags.index('SPL-AUTOCOMPLETE') < tags.index(expected),
                    'APPLICATION_PROFILE_AUTOCOMPLETE_CHANGED')
            for wrapper in host.locator('spl-input,spl-autocomplete').all():
                if wrapper.evaluate(_COMPOSED_CONTAINS, locator.element_handle()):
                    require(bool(locator.get_attribute('id')) and wrapper.get_attribute('id') in (None, '', locator.get_attribute('id')),
                            'APPLICATION_PROFILE_AUTOCOMPLETE_CHANGED')
                    wrappers.append(wrapper)
            info = {**info, 'kind': 'text', 'widget_type': 'spl-profile-text-v1'}
        else:
            require(info['tag'] in ('input', 'select', 'textarea') and not locator.get_attribute('role'),
                    'APPLICATION_PROFILE_EDITOR_CHANGED')
        for wrapper in wrappers:
            inputs = [item for item in wrapper.locator('input,select,textarea,[role="textbox"],[role="combobox"]').all()
                      if item.is_visible() and not (calendar is not None and calendar.evaluate(_COMPOSED_CONTAINS, item.element_handle()))]
            require(len(inputs) == 1 and inputs[0].evaluate('(el, wanted)=>el===wanted', locator.element_handle()),
                    'APPLICATION_PROFILE_EDITOR_CHANGED')
        if wrappers:
            info['required'] = info['required'] or any(wrapper.evaluate("el=>el.required===true||el.hasAttribute('required')||el.getAttribute('aria-required')==='true'") for wrapper in wrappers)
            info['disabled'] = info['disabled'] or any(wrapper.evaluate("el=>el.disabled===true||el.hasAttribute('disabled')||el.getAttribute('aria-disabled')==='true'") for wrapper in wrappers)
        require(info['kind'] == binding['kind'] and info['label'] == binding['label']
                and info['required'] is binding['required'] and not info['disabled'], 'APPLICATION_PROFILE_EDITOR_CHANGED')
        return locator, info, wrappers, clear, calendar

    def _profile_editor_controls(self, plan, editor, opened_calendar=None):
        controls, wrappers, clear, calendars = [], [], [], []
        for binding in plan['fields']:
            locator, info, owned, actions, calendar = self._profile_widget_context(binding, editor)
            require(not any(locator.evaluate('(el, other)=>el===other', prior.element_handle()) for prior, _ in controls),
                    'APPLICATION_PROFILE_EDITOR_CHANGED')
            controls.append((locator, info)); wrappers.extend(owned); clear.extend(actions)
            if calendar is not None: calendars.append(calendar)
        if opened_calendar is not None:
            require(any(opened_calendar.evaluate('(el, other)=>el===other', known.element_handle()) for known in calendars),
                    'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        for raw, _ in self._controls(editor):
            covered = any(raw.evaluate('(el, other)=>el===other', known.element_handle()) for known in [*(x for x, _ in controls), *wrappers])
            if opened_calendar is not None and opened_calendar.evaluate(_COMPOSED_CONTAINS, raw.element_handle()): covered = True
            require(covered, 'APPLICATION_PROFILE_EDITOR_CHANGED')
        return controls, clear

    def _profile_editor_surface_in(self, editor, plan, opened_calendar=None):
        self._profile_guard()
        controls, clear = self._profile_editor_controls(plan, editor, opened_calendar)
        actions = []
        for button in editor.locator('button,input[type="button"],input[type="submit"],input[type="reset"],a[href],[role="button"]').all():
            if button.is_visible():
                if any(button.evaluate('(el, other)=>el===other', action.element_handle()) for action in clear): continue
                if opened_calendar is not None and opened_calendar.evaluate(_COMPOSED_CONTAINS, button.element_handle()): continue
                label = button.get_attribute('aria-label') or button.inner_text().strip()
                require(button.evaluate("el=>el.tagName==='BUTTON'") and label in ('Save', 'Cancel'), 'APPLICATION_PROFILE_EDITOR_CHANGED')
                actions.append({'label': label, 'disabled': not button.is_enabled(), 'type': button.get_attribute('type') or ''})
        require(sum(a['label'] == 'Save' for a in actions) == 1, 'APPLICATION_PROFILE_EDITOR_CHANGED')
        shape = {'controls': sorted([i for _, i in controls], key=lambda x: json.dumps(x, sort_keys=True)),
                 'actions': sorted(actions, key=lambda x: json.dumps(x, sort_keys=True))}
        require(digest(shape) == plan['fingerprint'], 'APPLICATION_PROFILE_EDITOR_CHANGED')
        self._profile_button(editor, plan['save'])
        return editor

    def _profile_month_calendar_snapshot(self, binding, editor):
        self._profile_guard()
        locator, _, _, _, calendar = self._profile_widget_context(binding, editor)
        require(calendar is not None and calendar.is_visible(), 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        year_control = calendar.get_by_role('spinbutton', name='Year', exact=True)
        previous = calendar.get_by_role('button', name='Previous year', exact=True)
        following = calendar.get_by_role('button', name='Next year', exact=True)
        require(all(x.count() == 1 and x.is_visible() for x in (year_control, previous, following))
                and year_control.evaluate("el=>el.tagName==='INPUT'&&el.type==='number'")
                and bool(re.fullmatch(r'[0-9]{4}', year_control.input_value())), 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        year = int(year_control.input_value())
        cells = self._visible(calendar.get_by_role('gridcell'))
        require(len(cells) == 12, 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        months, metadata = {}, []
        for cell in cells:
            found = [i for i, name in enumerate(_MONTHS, 1) if cell.get_attribute('aria-label') == f'{name} 1, {year}']
            require(len(found) == 1 and found[0] not in months and cell.inner_text().strip() == _MONTHS[found[0] - 1][:3],
                    'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
            months[found[0]] = cell
            metadata.append({'month': found[0], 'tag': cell.evaluate('el=>el.tagName'), 'role': cell.get_attribute('role')})
        known = [year_control, previous, following, *cells]
        # The active dialog does not exempt unknown logical controls. Reuse
        # required-wrapper observation, then cover other interactive ARIA roles
        # even when they deliberately have no tab stop.
        for control, _ in self._controls(calendar):
            require(any(control.evaluate('(el, other)=>el===other', x.element_handle()) for x in known), 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        for control in calendar.locator('input,select,textarea,button,a[href],[role="button"],[role="gridcell"],'
                '[role="textbox"],[role="combobox"],[role="radio"],[role="radiogroup"],[role="checkbox"],'
                '[role="spinbutton"],[role="slider"],[role="switch"],[role="listbox"],[role="option"],'
                '[role="menuitem"],[required],[aria-required="true"],[contenteditable="true"],[tabindex]').all():
            semantic = control.evaluate("el=>['INPUT','SELECT','TEXTAREA','BUTTON','A'].includes(el.tagName)||['button','gridcell','textbox','combobox','radio','radiogroup','checkbox','spinbutton','slider','switch','listbox','option','menuitem'].includes(el.getAttribute('role'))||el.hasAttribute('required')||el.getAttribute('aria-required')==='true'||el.isContentEditable")
            if control.is_visible() and (semantic or control.get_attribute('tabindex') != '-1'):
                require(any(control.evaluate('(el, other)=>el===other', x.element_handle()) for x in known), 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        shape = {'year': {'tag': 'INPUT', 'type': 'number', 'label': 'Year',
                          'min': year_control.get_attribute('min'), 'max': year_control.get_attribute('max'), 'step': year_control.get_attribute('step')},
                 'previous': previous.evaluate("el=>({tag:el.tagName,role:el.getAttribute('role')})"),
                 'next': following.evaluate("el=>({tag:el.tagName,role:el.getAttribute('role')})"),
                 'months': sorted(metadata, key=lambda x: x['month'])}
        return dict(calendar=calendar, year=year, previous=previous, next=following, months=months, fingerprint=digest(shape))

    def _profile_readback(self, binding, editor):
        if 'month_picker' in binding:
            locator, _, _, _, _ = self._profile_widget_context(binding, editor)
            raw = locator.input_value()
            # Y-m-d is the reviewed month widget's transport. The UI's internal
            # first day is NEVER promoted to a candidate day/date fact.
            return binding['value'] if raw == (binding['value'] + '-01' if binding['value'] else '') else None
        if 'text_autocomplete' in binding:
            locator, _, _, _, _ = self._profile_widget_context(binding, editor)
            return locator.input_value()
        return self.readback(binding, root=editor)

    def _profile_write(self, binding, editor, plan):
        if self._profile_readback(binding, editor) == binding['value']: return
        if 'text_autocomplete' in binding:
            locator, _, _, _, _ = self._profile_widget_context(binding, editor)
            self._entry_effect_attempts += 1
            locator.fill(binding['value']); locator.blur()
            return
        if 'month_picker' not in binding:
            self.write(binding, root=editor)
            return
        locator, _, _, clear, _ = self._profile_widget_context(binding, editor)
        if not binding['value']:
            require(binding['required'] is False and len(clear) == 1, 'APPLICATION_PROFILE_MONTH_CLEAR_REQUIRED')
            self._entry_effect_attempts += 1
            clear[0].click()
            self._profile_guard()
            require(locator.input_value() == '', 'APPLICATION_PROFILE_READBACK_MISMATCH')
            return
        year, month = (int(x) for x in binding['value'].split('-'))
        self._entry_effect_attempts += 1
        locator.click()
        for _ in range(101):
            state = self._profile_month_calendar_snapshot(binding, editor)
            require(state['fingerprint'] == binding['month_picker']['calendar_fingerprint'] and abs(year - state['year']) <= 100,
                    'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
            self._profile_editor_surface_in(editor, plan, opened_calendar=state['calendar'])
            if state['year'] == year: break
            direction = 'previous' if year < state['year'] else 'next'
            require(state[direction].is_enabled(), 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
            self._entry_effect_attempts += 1
            state[direction].click()
            changed = self._profile_month_calendar_snapshot(binding, editor)
            require(changed['year'] == state['year'] + (-1 if direction == 'previous' else 1), 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        else:
            raise PermissionError('APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        cell = state['months'][month]
        require(cell.is_enabled() and cell.get_attribute('aria-disabled') != 'true'
                and 'flatpickr-disabled' not in (cell.get_attribute('class') or '').split(), 'APPLICATION_PROFILE_MONTH_CALENDAR_CHANGED')
        self._entry_effect_attempts += 1
        cell.click()
        state['calendar'].wait_for(state='hidden')
        self._profile_guard()
        require(self._profile_readback(binding, editor) == binding['value'], 'APPLICATION_PROFILE_READBACK_MISMATCH')

    def correct_reviewed_profile(self):
        """TEST adapter: client-only native editors, source-pinned complete plans.

        No upload/submit or endpoint phase is opened. Actual live Save behavior
        and editor compatibility remain unproven until separately observed.
        """
        step = self.manifest['steps'][0]
        phase = step.get('reviewed_profile_corrections')
        require(phase is not None and not self._profile_correction_started and not self._profile_corrections_applied,
                'APPLICATION_PROFILE_CORRECTION_FAILED')
        self._profile_guard()
        before = self.inspect()
        require(before['fingerprint'] == step['fingerprint'] and before['challenge'] is False
                and before['unexpected_write'] is False and before['unreviewed_prefill'] is False,
                'APPLICATION_PROFILE_SURFACE_BLOCKED')
        expected = {card['selector']: card['observed_text'] for card in phase['cards']}
        finals = {a['selector']: a['value'] for a in step['profile_assertions']}
        self._profile_correction_started = True
        for edit in phase['edits']:
            self._profile_guard()
            cards = self._profile_inventory(phase, expected)
            action = self._profile_button(cards[edit['card_selector']], edit['edit'])
            self._entry_effect_attempts += 1
            action.click()
            editor = self._profile_editor_surface(edit['editor'])
            for binding in edit['editor']['fields']:
                self._profile_inventory(phase, expected, editing=edit['card_selector'])
                editor = self._profile_editor_surface(edit['editor'])
                self._profile_write(binding, editor, edit['editor'])
                require(type(self._profile_readback(binding, editor)) is type(binding['value'])
                        and self._profile_readback(binding, editor) == binding['value'], 'APPLICATION_PROFILE_READBACK_MISMATCH')
            editor = self._profile_editor_surface(edit['editor'])
            require(all(type(self._profile_readback(b, editor)) is type(b['value'])
                        and self._profile_readback(b, editor) == b['value'] for b in edit['editor']['fields']),
                    'APPLICATION_PROFILE_READBACK_MISMATCH')
            self._profile_inventory(phase, expected, editing=edit['card_selector'])
            save = self._profile_button(editor, edit['editor']['save'])
            self._entry_effect_attempts += 1
            save.click()
            editor.wait_for(state='hidden')
            self._profile_guard()
            expected[edit['card_selector']] = finals[edit['card_selector']]
            self._profile_inventory(phase, expected)
        require(all(expected[card['selector']] == finals[card['selector']] for card in phase['cards']),
                'APPLICATION_PROFILE_READBACK_MISMATCH')
        self._profile_corrections_applied = True
        final = self.inspect()  # No pending mismatch allowance survives success.
        require(final['challenge'] is False and final['unexpected_write'] is False
                and final['unreviewed_prefill'] is False, 'APPLICATION_PROFILE_SURFACE_BLOCKED')

    def inspect(self):
        require(digest(self.manifest.get('entry_policy')) == self._entry_policy_pin, 'APPLICATION_ENTRY_POLICY_CHANGED')
        step = self.manifest['steps'][self._step]
        controls = self._controls()
        city_contexts = [(binding, self._city_context(binding)) for binding in step['fields'] if 'city_autocomplete' in binding]
        phone_contexts = [(binding, self._phone_country_context(binding)) for binding in step['fields'] if 'phone_country_select' in binding]
        city_wrappers = [wrapper for _, (_, _, wrappers, _, _) in city_contexts for wrapper in wrappers]
        logical = []
        for locator, info in controls:
            if any(locator.evaluate('(el, wrapper)=>el===wrapper', wrapper.element_handle()) for wrapper in city_wrappers): continue
            for _, (city, city_info, _, _, _) in city_contexts:
                if locator.evaluate('(el, city)=>el===city', city.element_handle()): info = city_info
            for _, (phone, phone_info, _, _, _) in phone_contexts:
                if locator.evaluate('(el, phone)=>el===phone', phone.element_handle()): info = phone_info
            logical.append((locator, info))
        controls = logical
        required_keys, covered = set(), set()
        unreviewed_prefill = False
        for binding in step['fields'] + ([step['upload']] if 'upload' in step else []):
            locator = self._locator(binding)
            info = (self._city_context(binding)[1] if 'city_autocomplete' in binding else
                    self._phone_country_context(binding)[1] if 'phone_country_select' in binding else locator.evaluate(_DESCRIBE))
            require(info['kind'] == binding['kind'] and info['label'] == binding['label']
                    and info['required'] is binding['required'] and not info['disabled'], 'APPLICATION_CONTROL_CHANGED')
            if info['required'] or binding is step.get('upload'):
                required_keys.add(binding['key'])
            for index, (observed, _) in enumerate(controls):
                if observed.evaluate('(el, other) => el===other', locator.element_handle()):
                    covered.add(index)
        for index, (locator, info) in enumerate(controls):
            if index not in covered:
                if info['required']:
                    required_keys.add('unknown:' + digest(info))
                if info['kind'] != 'file' and locator.evaluate(_VALUE) not in ('', False):
                    unreviewed_prefill = True
        # An exact structural fingerprint includes every visible control,
        # including optional fields/options; it contains no entered values/IDs.
        shape = sorted([info for _, info in controls], key=lambda x: json.dumps(x, sort_keys=True))
        body = self.page.locator('body').inner_text().casefold()
        frames_clear = (self._native_frames_clear() if self._native_entry_selected else
                        self._returned_frames_clear() if self._returned_entry_reviewed else
                        not self.page.locator('iframe').count())
        challenge = self._popup or not frames_clear or any(
            text in body for text in ('verify you are human', 'complete the captcha', 'verification code', 'access denied'))
        form_valid = self.page.locator('input,select,textarea').evaluate_all(
            "els => els.filter(x=>x.type!=='hidden'&&!x.disabled).every(x=>x.checkValidity())")
        submit = step.get('submit')
        submit_visible = bool(submit and self.page.locator(submit['selector']).count() == 1
                              and self.page.locator(submit['selector']).is_visible()
                              and self.page.locator(submit['selector']).is_enabled())
        # A reviewed profile card must have a fact-backed visible assertion.
        # Imported LinkedIn cards are never promoted to truth by matching a URL.
        assertions = step.get('profile_assertions', [])
        pending = self._pending_profile_cards(step)
        unreviewed_prefill = unreviewed_prefill or self._uncovered_profile_actions(step)
        for assertion in assertions:
            require(self.page.locator(assertion['selector']).count() == 1
                    and self.page.locator(assertion['selector']).inner_text().strip() == pending.get(assertion['selector'], assertion['value']),
                    'APPLICATION_IMPORTED_PROFILE_MISMATCH')
        return {'url': self.page.url, 'fingerprint': digest(shape), 'required_keys': required_keys,
                'challenge': challenge, 'unreviewed_prefill': unreviewed_prefill,
                'unexpected_write': self.boundary.blocked_write_count != 0 or self._external_surface,
                'form_valid': form_valid, 'submit_visible': submit_visible}

    def write(self, binding, *, root=None):
        if 'phone_country_select' in binding:
            require(root is None, 'APPLICATION_PHONE_COUNTRY_CHANGED')
            locator, _, host, option, _ = self._phone_country_context(binding)
            if self.readback(binding) == binding['value']: return
            self._entry_effect_attempts += 1
            locator.click()
            menu_id = locator.get_attribute('aria-controls')
            require(bool(menu_id) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', menu_id)), 'APPLICATION_PHONE_COUNTRY_CHANGED')
            menu = host.locator(f'[id="{menu_id}"]')
            require(menu.count() == 1 and menu.is_visible() and menu.get_attribute('role') == 'listbox'
                    and menu.evaluate("(menu, option)=>{for(let node=option;node;node=node.assignedSlot||node.parentElement||node.getRootNode().host)if(node===menu)return true;return false;}", option.element_handle())
                    and option.is_visible() and option.is_enabled() and option.get_attribute('disabled') is None
                    and option.get_attribute('aria-disabled') != 'true'
                    and option.locator('spl-dropdown-item').get_attribute('disabled') is None
                    and option.locator('spl-dropdown-item').get_attribute('aria-disabled') != 'true', 'APPLICATION_PHONE_COUNTRY_CHANGED')
            self._entry_effect_attempts += 1
            option.click()
            require(self.readback(binding) == binding['value'], 'APPLICATION_PHONE_COUNTRY_CHANGED')
            return
        if 'city_autocomplete' in binding:
            require(root is None, 'APPLICATION_CITY_AUTOCOMPLETE_CHANGED')
            locator, _, _, _, _ = self._city_context(binding)
            if locator.input_value() != binding['value']:
                self._entry_effect_attempts += 1
                locator.fill(binding['value']); locator.blur()
            require(not self._entry_network_fault and not self._external_surface and not self._popup
                    and self.boundary.blocked_write_count == 0 and locator.get_attribute('aria-invalid') != 'true'
                    and locator.evaluate('el=>el.checkValidity()') is True, 'APPLICATION_CITY_AUTOCOMPLETE_CHANGED')
            return
        locator = self._locator(binding, root)
        kind, value = binding['kind'], binding['value']
        if self.readback(binding, root=root) == value:
            return
        self._entry_effect_attempts += 1
        if kind == 'select':
            if locator.evaluate("el => el.tagName==='SELECT'"):
                locator.select_option(label=value)
            else:
                require(locator.get_attribute('role') == 'combobox', 'APPLICATION_SELECT_UNREVIEWED')
                locator.click()
                associated = locator.get_attribute('aria-controls')
                if associated:
                    require(bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', associated)), 'APPLICATION_SELECT_MENU_UNREVIEWED')
                    container = self.page.locator(f'[id="{associated}"]')
                    require(container.count() == 1 and container.is_visible()
                            and container.get_attribute('role') in ('listbox', 'menu'), 'APPLICATION_SELECT_MENU_UNREVIEWED')
                    containers = [container]
                else:
                    containers = self._visible(self.page.locator('[role="listbox"],[role="menu"]'))
                options = []
                for container in containers:
                    for option in self._visible(container.get_by_role('option', name=value, exact=True)):
                        self._append_unique(options, option)
                require(len(options) <= 1, 'APPLICATION_SELECT_OPTION_AMBIGUOUS')
                if not options:
                    spl_rows = []
                    for container in containers:
                        for row in container.locator('spl-select-option').all():
                            self._append_unique(spl_rows, row)
                    if spl_rows:
                        # Reviewed SPL rows have no option role. Their children
                        # may be display:contents and duplicate hidden tooltips;
                        # select the uniquely visible exact-text host itself.
                        require(bool(associated), 'APPLICATION_SELECT_MENU_UNREVIEWED')
                        options = [row for row in spl_rows if row.is_visible()
                                   and row.text_content().strip() == value]
                        require(len(options) == 1, 'APPLICATION_SELECT_OPTION_UNAVAILABLE')
                if not options:
                    # Some reviewed widgets expose text in a listbox/menu
                    # without option roles. Never fall back to arbitrary page
                    # text or pick the first of multiple visible matches.
                    for container in containers:
                        for option in self._visible(container.get_by_text(value, exact=True)):
                            self._append_unique(options, option)
                require(len(options) == 1, 'APPLICATION_SELECT_OPTION_UNAVAILABLE')
                options[0].click()
        elif kind == 'checkbox':
            locator.set_checked(value)
        elif kind == 'radio':
            require(value in ('Yes', 'No'), 'APPLICATION_RADIO_VALUE_UNREVIEWED')
            if locator.get_attribute('role') == 'radiogroup':
                radios = self._visible(locator.get_by_role('radio', name=value, exact=True))
                require(len(radios) == 1, 'APPLICATION_RADIO_OPTION_AMBIGUOUS')
                radios[0].check()
            else:
                locator.check()
        else:
            locator.fill(str(value))
            locator.blur()

    def readback(self, binding, *, root=None):
        if 'phone_country_select' in binding:
            require(root is None, 'APPLICATION_PHONE_COUNTRY_CHANGED')
            _, _, _, _, current = self._phone_country_context(binding)
            # A shared dial code cannot make a different country a match.
            return (None if current['iso2'] != binding['phone_country_select']['country_iso2']
                    and current['dial'] == binding['value'] else current['dial'])
        if 'city_autocomplete' in binding:
            require(root is None, 'APPLICATION_CITY_AUTOCOMPLETE_CHANGED')
            return self._city_context(binding)[0].input_value()
        locator = self._locator(binding, root)
        value = locator.evaluate(_VALUE)
        if binding['kind'] == 'radio':
            if locator.get_attribute('role') == 'radiogroup':
                require(value != 'AMBIGUOUS_RADIO_SELECTION', 'APPLICATION_RADIO_SELECTION_AMBIGUOUS')
                return value
            return binding['value'] if value is True else ''
        if binding['kind'] == 'number' and type(binding['value']) in (int, float):
            try:
                return type(binding['value'])(value)
            except (ValueError, TypeError):
                return None
        return value

    def _phone_country_context(self, binding):
        """Exact reviewed SPL phone select; never infer a nearest host value."""
        spec = binding['phone_country_select']
        require(binding.get('canonical_field') == 'candidate.phone_country_code' and binding.get('label') == 'Country code'
                and binding.get('kind') == 'select' and binding.get('resolution_mode') == 'Verified Fact'
                and type(spec) is dict and set(spec) == {'type', 'host_selector', 'country_iso2', 'country_label', 'option_label', 'country_provenance_ref'}
                and spec['type'] == 'spl-phone-country-v1' and self.page is self._owned_page
                and self.context is self._owned_context and self.boundary is self._owned_boundary
                and self.page.url == self._owned_target.entry_url and self._step == 0
                and not self._submit_called and not self._entry_network_fault and not self._external_surface and not self._popup
                and self.boundary.blocked_write_count == 0, 'APPLICATION_PHONE_COUNTRY_CHANGED')
        locator = self._locator(binding)
        info = locator.evaluate(_DESCRIBE)
        host = self.page.locator(spec['host_selector'])
        require(host.count() == 1 and host.evaluate("el=>el.tagName==='SPL-SELECT'&&el.classList.contains('c-spl-phone-field-select')")
                and locator.evaluate("(el, host)=>el.tagName==='BUTTON'&&el.getAttribute('role')==='combobox'&&el.getRootNode().host===host", host.element_handle())
                and bool(host.get_attribute('id')) and locator.get_attribute('id') == host.get_attribute('id')
                and info['kind'] == 'select' and info['label'] == binding['label']
                and info['required'] is binding['required'] and not info['disabled'] and locator.is_enabled()
                and host.get_attribute('disabled') is None and host.get_attribute('aria-disabled') != 'true'
                and host.get_attribute('splarialabel') == binding['label'], 'APPLICATION_PHONE_COUNTRY_CHANGED')
        options, selected, metadata, target = host.locator('spl-select-option').all(), [], [], []
        require(0 < len(options) <= 300, 'APPLICATION_PHONE_COUNTRY_CHANGED')
        for option in options:
            iso, label = option.get_attribute('value'), option.get_attribute('label')
            text = ' '.join(option.text_content().split())
            require(type(iso) is str and bool(re.fullmatch(r'[A-Z]{2}', iso)) and bool(label)
                    and option.evaluate("el=>el.classList.contains('c-spl-phone-field-select-option')")
                    and bool(re.fullmatch(re.escape(label) + r' \+[1-9][0-9]{0,3}', text)), 'APPLICATION_PHONE_COUNTRY_CHANGED')
            items = option.locator('spl-dropdown-item')
            require(items.count() == 1 and items.get_attribute('selected') in (None, ''), 'APPLICATION_PHONE_COUNTRY_CHANGED')
            if items.get_attribute('selected') is not None: selected.append((iso, label, text))
            if iso == spec['country_iso2']:
                require(label == spec['country_label'] and text == spec['option_label'], 'APPLICATION_PHONE_COUNTRY_CHANGED')
                target.append(option)
            metadata.append(iso + '|' + label + '|' + text)
        require(len(target) == len(selected) == 1 and len({item.split('|')[0] for item in metadata}) == len(metadata), 'APPLICATION_PHONE_COUNTRY_CHANGED')
        iso, label, text = selected[0]
        require(host.get_attribute('value') == iso and host.evaluate('(el, iso)=>typeof el.value!=="string"||el.value===iso', iso), 'APPLICATION_PHONE_COUNTRY_CHANGED')
        slot = locator.locator('slot[name="triggerPrefix"]')
        prefix = host.locator('[slot="triggerPrefix"].c-spl-phone-field-selected')
        require(slot.count() == prefix.count() == 1 and prefix.is_visible() and prefix.evaluate("el=>el.tagName==='DIV'")
                and slot.evaluate("(el, prefix)=>{const nodes=el.assignedNodes({flatten:true}).filter(x=>x.nodeType===1||x.textContent.trim());return nodes.length===1&&nodes[0]===prefix;}", prefix.element_handle()), 'APPLICATION_PHONE_COUNTRY_CHANGED')
        flag, dial = prefix.locator('spl-country-flag'), prefix.locator('spl-typography-body')
        require(flag.count() == dial.count() == 1 and flag.is_visible() and dial.is_visible()
                and flag.get_attribute('countryisocode') == iso and flag.get_attribute('alt') == label
                and ' '.join(dial.inner_text().split()) == text[len(label) + 1:], 'APPLICATION_PHONE_COUNTRY_CHANGED')
        return locator, {**info, 'widget_type': 'spl-phone-country-v1', 'options': sorted(metadata)}, host, target[0], dict(iso2=iso, dial=dial.inner_text().strip())

    def _city_context(self, binding):
        """One ordinary candidate.city binding; never a generic select fallback."""
        spec = binding['city_autocomplete']
        require(binding.get('canonical_field') == 'candidate.city' and binding.get('label') == 'City'
                and binding.get('kind') == 'text' and binding.get('resolution_mode') == 'Verified Fact'
                and type(spec) is dict and set(spec) == {'type', 'host_selector'} and spec['type'] == 'spl-city-text-v1'
                and self.page is self._owned_page and self.context is self._owned_context and self.boundary is self._owned_boundary
                and self.page.url == self._owned_target.entry_url and self._step == 0 and not self._submit_called
                and not self._entry_network_fault and not self._external_surface and not self._popup,
                'APPLICATION_CITY_AUTOCOMPLETE_CHANGED')
        # Reuse only the strict ownership/metadata validator. This does not
        # activate profile editing or reinterpret any other combobox as text.
        proxy = {**binding, 'text_autocomplete': {'type': 'spl-profile-text-v1', 'host_selector': spec['host_selector']}}
        locator, info, wrappers, clear, calendar = self._profile_widget_context(proxy, self.page)
        return locator, {**info, 'widget_type': 'spl-city-text-v1'}, wrappers, clear, calendar

    @staticmethod
    def _visible(locator):
        return [item for item in locator.all() if item.is_visible()]

    @staticmethod
    def _append_unique(items, candidate):
        if not any(candidate.evaluate('(el, other) => el===other', prior.element_handle()) for prior in items):
            items.append(candidate)

    def upload(self, binding, payload, filename):
        require(self._upload is None and self.boundary.blocked_write_count == 0)
        locator = self._locator(binding)
        require(locator.evaluate('(el)=>el.type') == 'file')
        endpoint = self.boundary.target.resume_upload_url(filename)
        self._entry_effect_attempts += 1
        self.boundary.begin_upload()
        try:
            with self.page.expect_response(lambda r: r.url == endpoint and r.request.method == 'POST', timeout=30_000) as pending:
                locator.set_input_files({'name': filename, 'mimeType': 'application/pdf', 'buffer': payload})
            response = pending.value
            require(response.status in (200, 201) and response.request.redirected_from is None)
            data = response.json()
            # Explicit vendor attachment metadata, not a synthetic readback SHA.
            require(isinstance(data, dict) and isinstance(data.get('fileId'), str) and bool(data['fileId'])
                    and data.get('fileName') == filename, 'APPLICATION_UPLOAD_UNVERIFIED')
            require(self.page.get_by_text(filename, exact=True).count() == 1
                    and self.page.get_by_text(filename, exact=True).is_visible(), 'APPLICATION_UPLOAD_UNVERIFIED')
            self._upload = UploadObservation(hashlib.sha256(payload).hexdigest(), len(payload), filename,
                hashlib.sha256(data['fileId'].encode()).hexdigest(), endpoint, True)
            return self._upload
        finally:
            self.boundary.end_upload()

    def advance(self, binding, expected_url):
        require(self._step + 1 < len(self.manifest['steps'])
                and expected_url == self.manifest['steps'][self._step + 1]['url']
                and self.boundary.blocked_write_count == 0)
        locator = self._locator(binding)
        require(locator.inner_text().strip() == binding['label'] and binding['label'] in ('Next', 'Continue'))
        self._entry_effect_attempts += 1
        locator.click()
        self.page.wait_for_url(expected_url, timeout=15_000)
        self._step += 1

    def submit_once(self, binding):
        require(not self._submit_called and self.boundary.blocked_write_count == 0
                and self.boundary.application_attempts == 0)
        locator = self._locator(binding)
        require(locator.inner_text().strip() == binding['label'] and binding['label'] in ('Submit', 'Submit application', 'Send application'))
        self._submit_called = True
        self._entry_effect_attempts += 1
        self.boundary.begin_submit()
        with self.page.expect_response(lambda r: r.url == self.boundary.target.application_url
                                      and r.request.method == 'POST', timeout=30_000) as pending:
            locator.click()
        response = pending.value
        require(response.request.redirected_from is None)
        payload = response.body()
        require(len(payload) <= 1_000_000)
        data = json.loads(payload)
        # Actual schema is independently reviewed; absent identity is uncertainty.
        success = isinstance(data, dict) and data.get('success') is True
        confirmation = self.manifest['confirmation']
        marker = self.page.locator(confirmation['selector'])
        marker.wait_for(state='visible', timeout=15_000)
        success_url = self.boundary.target.screening_url.replace('/screening?', '/success?')
        visible = marker.inner_text().strip() == confirmation['text'] and self.page.url == success_url
        return ConfirmationObservation(response.url, datetime.now(timezone.utc), response.status,
            hashlib.sha256(payload).hexdigest(), success, visible,
            self._popup or self.boundary.application_attempts != 1 or self.boundary.blocked_write_count != 0)
