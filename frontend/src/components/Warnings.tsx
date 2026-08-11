import { WarningCircle } from "@phosphor-icons/react";

/** A banner from `warnings[]`. Today the backend generates NONE — the warning about a series in
 *  the `unknown` state disappeared together with the very notion in the UI, because the
 *  reading-age label says the same thing with a number of minutes instead of a state name.
 *
 *  The component stays, because `warnings[]` stays in the contract: the field is the place for
 *  facts above the accounts, which an account card has no way to show. An empty `warnings[]` is
 *  a correct state, not a missing implementation. */
export function Warnings({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <>
      {items.map((text) => (
        <div className="banner" key={text} role="status">
          <WarningCircle size={16} className="banner-icon" />
          <div className="banner-lines">
            <span className="banner-text">{text}</span>
            <span className="banner-source">
              from the <code>warnings[]</code> field of the /status response
            </span>
          </div>
        </div>
      ))}
    </>
  );
}
