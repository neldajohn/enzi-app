function triggerPhotoInput(inputId) {
  var input = document.getElementById(inputId);
  if (!input) return;
  input.click();
}

function showPhotoFilename(inputId) {
  var input = document.getElementById(inputId);
  var label = document.getElementById('photo-filename-' + inputId);
  if (!input || !label) return;
  label.textContent = input.files && input.files.length ? '✓ ' + input.files[0].name : '';
}

function toggleNewVendorFields(selectEl, fieldsId) {
  var fields = document.getElementById(fieldsId || 'new-vendor-fields');
  if (!fields) return;
  fields.hidden = selectEl.value !== '__new__';
}

function guardDoubleSubmit(formEl) {
  var btn = formEl.querySelector('button[type="submit"]');
  if (btn) {
    if (btn.disabled) return false;
    btn.disabled = true;
  }
  return true;
}
