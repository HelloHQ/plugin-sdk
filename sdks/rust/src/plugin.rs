// SPDX-License-Identifier: Apache-2.0
//
//! The plugin entry-point: the [`Plugin`] trait + the [`export_plugin!`] macro.

use alloc::string::String;
use alloc::vec::Vec;

use crate::PluginMetadata;

/// The three exports every HelloHQ Tier-2 plugin provides (the canonical
/// `hellohq:plugin/guest` interface).
///
/// Implement this on a unit struct and wire it with [`export_plugin!`].
///
/// ```ignore
/// struct MyPlugin;
/// impl hellohq_plugin_sdk::Plugin for MyPlugin {
///     fn init() {}
///     fn run(input: alloc::vec::Vec<u8>) -> Result<alloc::vec::Vec<u8>, alloc::string::String> {
///         Ok(input) // echo
///     }
///     fn metadata() -> hellohq_plugin_sdk::PluginMetadata {
///         hellohq_plugin_sdk::PluginMetadata { id: "my-plugin".into(), version: "0.1.0".into() }
///     }
/// }
/// hellohq_plugin_sdk::export_plugin!(MyPlugin);
/// ```
pub trait Plugin {
    /// Called once after the component is instantiated, before any `run`.
    /// Use it to log a banner or warm caches. Default: no-op.
    fn init() {}

    /// The plugin's main entry point. `input` is opaque bytes from the host;
    /// the returned bytes are handed back. On `Err`, the host surfaces the
    /// string (and degrades the pane gracefully).
    fn run(input: Vec<u8>) -> Result<Vec<u8>, String>;

    /// Static identity reported to the host.
    fn metadata() -> PluginMetadata;
}

/// Wire a [`Plugin`] implementation into the canonical `hellohq:plugin/guest`
/// component exports.
///
/// Generates the `Guest` impl over the generated bindings and calls the
/// generated `export!` so the component exports `init` / `run` / `metadata`.
///
/// ```ignore
/// hellohq_plugin_sdk::export_plugin!(MyPlugin);
/// ```
#[macro_export]
macro_rules! export_plugin {
    ($ty:ident) => {
        // Adapt the author's `Plugin` impl onto the generated guest trait.
        const _: () = {
            use $crate::bindings::exports::hellohq::plugin::guest::{
                Guest as __HqGuest, PluginMetadata as __HqMetadata,
            };

            impl __HqGuest for $ty {
                fn init() {
                    <$ty as $crate::Plugin>::init();
                }

                fn run(
                    input: $crate::__alloc::vec::Vec<u8>,
                ) -> ::core::result::Result<
                    $crate::__alloc::vec::Vec<u8>,
                    $crate::__alloc::string::String,
                > {
                    <$ty as $crate::Plugin>::run(input)
                }

                fn metadata() -> __HqMetadata {
                    <$ty as $crate::Plugin>::metadata()
                }
            }
        };

        // Invoke the generated `#[macro_export]` export macro (lives at the SDK
        // crate root because of `pub_export_macro`). It fills in
        // `with_types_in <default_bindings_module>` itself.
        $crate::__export_hellohq_plugin_impl!($ty);
    };
}
