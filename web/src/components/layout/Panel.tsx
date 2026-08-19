/**
 * The frosted panel that holds the whole UI.
 *
 * Two shapes, one component. Below `sm` it is a bottom sheet: a flex row in the
 * page's column that takes the lower part of the screen while the map keeps the
 * upper part, because a phone-width panel floating over the map hides the map
 * completely and the product is the two of them together. From `sm` up it goes
 * back to a floating side panel, and `pointer-events-none` on the wrapper with
 * `pointer-events-auto` on the panel is what lets the map stay draggable
 * everywhere the panel is not.
 *
 * Width is capped rather than proportional past `lg`: a 2,560-pixel monitor
 * wants more map, not a form stretched across half of it.
 *
 * `@container` makes the panel a query container, so the content inside can
 * respond to how wide the *panel* is. Viewport breakpoints are the wrong
 * question here — the panel is 380 px on a tablet and 320 px on a phone, and a
 * `sm:` rule would treat those as opposites.
 */
export default function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="pointer-events-none flex min-h-0 flex-1 flex-col sm:absolute sm:inset-0 sm:flex-row sm:justify-end sm:p-4">
      <section
        aria-label="Plan a walk"
        className="@container pointer-events-auto flex min-h-0 w-full flex-1 flex-col gap-3 overflow-y-auto rounded-t-2xl border-t border-line bg-ground/92 p-4 backdrop-blur-md sm:w-[58%] sm:max-w-[420px] sm:flex-none sm:rounded-2xl sm:border lg:w-full lg:max-w-[430px] 2xl:max-w-[480px]"
      >
        {children}
      </section>
    </div>
  );
}
