//! Ambire boards (`[ambire_branding]`): the strings that name Trezor, its app
//! (Trezor Suite) or its website, reworded for Ambire.
//!
//! `TranslatedString::translate` asks here first, so these win over both the
//! built-in English (generated from `core/translations/en.json`, which stays
//! as upstream has it) and any translation blob. Keyed by name: a string that
//! upstream renames or drops fails to compile here, and a new string naming
//! Trezor shows up in `firmware/branding/check_strings.py`.
//!
//! "Ambire" has as many letters as "Trezor" and is ~9 px wider in the 21 px
//! font, so the one-for-one swaps wrap as before. Titles where "the Ambire
//! app" does not fit say "App".

use super::TR;

pub fn get(s: TR) -> Option<&'static str> {
    Some(match s {
        TR::addr_mismatch__contact_support_at => "Please contact Ambire support at",
        TR::addr_mismatch__support_url => "ambire.com",
        TR::address__cancel_contact_support => {
            "If receive address doesn't match, contact Ambire Support at ambire.com."
        }
        TR::auto_lock__change_template => "Auto-lock Ambire after {0} of inactivity?",
        TR::auto_lock__description => "Set the time before your Ambire locks automatically.",
        TR::coinjoin__do_not_disconnect => "Do not disconnect your Ambire!",
        TR::firmware_update__restart => "Ambire will restart after update.",
        TR::misc__title_suite_labeling => "App labeling",
        TR::passphrase__always_on_device => "Always enter your passphrase on Ambire?",
        TR::reboot_to_bootloader__restart => "Ambire will restart in bootloader mode.",
        TR::reset__by_continuing => "By continuing you agree to Ambire's terms and conditions.",
        TR::reset__tos_link => "ambire.com",
        TR::safety_checks__approve_unsafe_always => {
            "Ambire will allow you to approve some actions which might be unsafe."
        }
        TR::safety_checks__approve_unsafe_temporary => {
            "Ambire will temporarily allow you to approve some actions which might be unsafe."
        }
        TR::sd_card__restart => "Do you want to restart Ambire in bootloader mode?",
        #[cfg(feature = "universal_fw")]
        TR::stellar__transaction_source_diff_warning => {
            "Transaction source does not belong to this Ambire."
        }
        TR::suite_sync__delegated_identity_key_no_thp => {
            "Allow the Ambire app to use app sync with this Ambire?"
        }
        TR::suite_sync__delegated_identity_key_thp => {
            "Allow {0} on {1} to use app sync with this Ambire?"
        }
        TR::suite_sync__header => "App sync",
        TR::suite_sync__rotate_key => {
            "You are about to rotate the delegated identity key used for app sync. This will invalidate previous keys. Do you want to proceed?"
        }
        TR::tutorial__first_wallet => "The world's first hardware wallet\nwas created in 2013.",
        TR::tutorial__welcome_safe5 => "Welcome to\nAmbire",
        TR::wipe__start_again => "Wipe your Ambire and start the setup process again.",
        TR::words__my_trezor => "My Ambire",
        _ => return None,
    })
}
