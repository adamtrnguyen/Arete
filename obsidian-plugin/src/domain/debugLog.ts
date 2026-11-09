/**
 * Diagnostic logging behind the `debug_mode` setting. The plugin points the switch at
 * its settings on load; until then, and whenever debug mode is off, nothing is logged.
 * Real failures use console.error directly and are not gated.
 */

let enabled: () => boolean = () => false;

/** Read debug mode from `isEnabled` on every call, so a settings change applies at once. */
export function setDebugLogSwitch(isEnabled: () => boolean): void {
	enabled = isEnabled;
}

/** console.log when debug mode is on; nothing otherwise. */
export function debugLog(...args: unknown[]): void {
	if (enabled()) console.log(...args);
}
