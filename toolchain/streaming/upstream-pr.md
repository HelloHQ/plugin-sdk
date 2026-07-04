# Upstream fix to file against `dicej/componentize-js`

Repo: https://github.com/dicej/componentize-js
Patch: `patches/0001-pop-record-reverse-field-order.patch`

## Title

fix(runtime): lower record fields in reverse so they don't come out swapped

## Body

`MyCall::pop_record` pushes a record's field values onto the value stack in
**forward** declaration order, but `pop_tuple` (and the rest of the lowering)
expects the first element on **top** of the LIFO stack — `pop_tuple` already
compensates by pushing in reverse. As a result, record fields are lowered in
reverse, so a record argument passed from JS has its fields swapped.

### Symptoms

For `record inference-opts { max-tokens: u32, temperature: option<f32> }` called
as `complete(messages, { maxTokens: 64, temperature: undefined })`:

- the `option` none (`undefined`) is read where the `u32` is expected →
  `assertion failed: self.is_number()` (mozjs `jsval.rs`), process abort;
- with a numeric temperature the build runs but values swap (`max-tokens` reads
  `temperature`, etc.). `record { role, content }` similarly swaps.

### Fix

Mirror `pop_tuple`: collect fields and push in reverse.

```rust
fn pop_record(&mut self, ty: wit::Record) {
    let cx = &mut context();
    rooted!(&in(cx) let record = self.pop().to_object());
    let fields = ty.fields().collect::<Vec<_>>();
    for (name, _) in fields.into_iter().rev() {
        self.push(get(cx, record.handle(), &CString::new(mangle_name(name)).unwrap()));
    }
}
```

### Verified

Built a JS component importing `inference.complete -> result<stream<string>,
api-error>` and a `{role,content}` / `{max-tokens, temperature: option<f32>}`
record path; before the fix it aborted on the `option` none, after the fix
`max-tokens=64`, the prompt string, and the drained `stream<string>` are all
correct, run on a wasmtime component-model-async host.

(Existing repo tests don't pass a record from JS — only `wasi:cli`/`wasi:http`
resource methods — which is why this stayed latent; consider adding a record
round-trip to `tests`.)
