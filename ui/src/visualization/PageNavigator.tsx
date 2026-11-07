import { MEASURES_PER_PAGE } from "./playback";

/** Page prev/next plus per-measure seek buttons for the current page. */
export default function PageNavigator({ page, pages, onPage, onMeasure }: {
  page: number; pages: number; onPage: (page: number) => void; onMeasure: (bar: number) => void;
}) {
  return <>
    <div className="page-controls"><button disabled={page === 0} onClick={() => onPage(page - 1)}>Previous measures</button>
      <span>Page {page + 1} of {pages}</span><button disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>Next measures</button></div>
    <div className="measure-seek" role="group" aria-label="Seek to measure on this page">
      {Array.from({ length: MEASURES_PER_PAGE }, (_, bar) => <button key={bar} onClick={() => onMeasure(bar)}>Measure {page * MEASURES_PER_PAGE + bar + 1}</button>)}
    </div>
  </>;
}
