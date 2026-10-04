// CI's commit message rules: the local ones (commitlint.config.mjs), plus Dependabot's security updates, whose
// messages can't carry our sections. They reach develop only through GitHub, when the founder merges one of
// Dependabot's pull requests, so only CI's check of a push skips them. A commit is skipped only when Dependabot's
// sign-off is its last line; the local commit-msg hook never skips it, so no local commit can use the line.
import base from "./commitlint.config.mjs";

const DEPENDABOT_SIGN_OFF = /\nSigned-off-by: dependabot\[bot\] <support@github\.com>\s*$/;

export default { ...base, ignores: [(message) => DEPENDABOT_SIGN_OFF.test(message)] };
