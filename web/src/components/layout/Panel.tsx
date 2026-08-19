/**
 * The frosted side panel that holds the whole UI.
 *
 * `pointer-events-none` on the wrapper with `pointer-events-auto` on the panel
 * is what lets the map stay draggable everywhere the panel is not.
 */
export default function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="pointer-events-none absolute inset-0 flex justify-end p-3 sm:p-4">
      <section
        aria-label="Plan a walk"
        className="pointer-events-auto flex w-full max-w-[430px] flex-col gap-3 overflow-y-auto rounded-2xl border border-line bg-ground/92 p-4 backdrop-blur-md"
      >
        {children}
      </section>
    </div>
  );
}
