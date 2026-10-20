const arrows=['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'] as const;
export function chipNavigationKey(event:KeyboardEvent):typeof arrows[number]|null{
  if(event.defaultPrevented||event.isComposing||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey||!arrows.includes(event.key as typeof arrows[number]))return null;
  if(event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable],[role="textbox"],[role="combobox"],[role="slider"],[role="spinbutton"],[role="listbox"],[role="menu"],[role="tablist"],[role="radiogroup"],dialog,[role="dialog"],[role="alertdialog"]'))return null;
  return event.key as typeof arrows[number];
}
