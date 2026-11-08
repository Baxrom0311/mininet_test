/** Reveal invalid controls inside disclosure panels before asking the browser to focus them. */
export function validInputs(root:ParentNode|null) {
  if(!root) return false
  for(const input of root.querySelectorAll<HTMLInputElement>('input')) {
    if(input.validity.valid) continue
    let parent=input.parentElement
    while(parent) {if(parent instanceof HTMLDetailsElement) parent.open=true;parent=parent.parentElement}
    input.scrollIntoView({block:'center'})
    input.reportValidity();return false
  }
  return true
}
