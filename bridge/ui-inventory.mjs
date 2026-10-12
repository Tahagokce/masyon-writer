// This runs against the active page's whole document, not the currently selected
// tabpanel. Nothing from the inventory is sent to Gemini unless resolution fails.
export async function uiInventory(page, {includeHidden = false} = {}) {
  return page.evaluate(includeHidden => {
    const tidy = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 300);
    const visible = element => {
      if (element.closest('[hidden],[aria-hidden="true"],[inert]')) return false;
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
      if (element.tagName === 'OPTION') return element.parentElement ? visible(element.parentElement) : false;
      return Boolean(element.getClientRects().length);
    };
    const roleOf = element => {
      const explicit = element.getAttribute('role')?.split(/\s+/)[0];
      if (explicit && !['none','presentation','generic'].includes(explicit)) return explicit;
      const tag = element.tagName.toLowerCase();
      if (tag === 'button') return 'button';
      if (tag === 'a' && element.hasAttribute('href')) return 'link';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'select') return 'combobox';
      if (tag === 'option') return 'option';
      if (tag === 'dialog') return 'dialog';
      if (tag === 'input') {
        const type = (element.getAttribute('type') || 'text').toLowerCase();
        if (['checkbox','radio','button','submit','reset'].includes(type))
          return ['submit','reset'].includes(type) ? 'button' : type;
        if (type === 'range') return 'slider';
        if (type === 'number') return 'spinbutton';
        return 'textbox';
      }
      return explicit || '';
    };
    const pathOf = element => {
      const parts = [];
      for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
        const tag = node.tagName.toLowerCase();
        const siblings = node.parentElement ? [...node.parentElement.children].filter(child => child.tagName === node.tagName) : [node];
        parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(node) + 1})`);
        if (tag === 'html') break;
      }
      return parts.join(' > ');
    };
    const labelText = label => {
      const copy = label.cloneNode(true);
      copy.querySelectorAll('input,select,textarea,button,option').forEach(control => control.remove());
      return tidy(copy.textContent);
    };
    const selectors = 'button,a[href],input,textarea,select,option,dialog,[role],[tabindex],[onclick],[contenteditable]';
    return [...document.querySelectorAll(selectors)].flatMap(element => {
      const role = roleOf(element);
      if (!role && !element.hasAttribute('tabindex') && !element.hasAttribute('onclick')) return [];
      const isVisible = visible(element);
      if (!includeHidden && !isVisible) return [];
      const ariaLabelledBy = tidy((element.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => document.getElementById(id)?.textContent || '').join(' '));
      const label = tidy(element.labels ? [...element.labels].map(labelText).join(' ') : '');
      const text = tidy(element.innerText || (element.tagName === 'OPTION' ? element.textContent : ''));
      const placeholder = tidy(element.getAttribute('placeholder'));
      const title = tidy(element.getAttribute('title'));
      const name = ariaLabelledBy || tidy(element.getAttribute('aria-label')) || label ||
        tidy(element.getAttribute('alt')) || text || title || placeholder ||
        (['submit','button','reset'].includes(element.getAttribute('type')) ? tidy(element.value) : '');
      return [{id:element.id || '',testId:element.getAttribute('data-testid') ||
          element.getAttribute('data-test-id') || element.getAttribute('data-qa') || '',
        role,name,text,label,placeholder,title,visible:isVisible,
        enabled:!element.disabled && element.getAttribute('aria-disabled') !== 'true',
        tag:element.tagName.toLowerCase(),inputType:element.tagName === 'INPUT'
          ? (element.getAttribute('type') || 'text').toLowerCase() : '',path:pathOf(element)}];
    });
  }, includeHidden);
}
