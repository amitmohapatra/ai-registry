/* Behavioral constants, one place — the UI mirror of backend config.py.
   Nothing that shapes list sizes, debounce, or paging lives inline. */
export const PAGE_SIZE = 25            // rows fetched per scroll step for tables
export const PICKER_LIMIT = 8          // rows shown in autocomplete pickers
export const SEARCH_DEBOUNCE_MS = 250  // wait after typing before a server search
export const CHECK_BOUNDARY_MS = 600   // similarity check after a word boundary
export const CHECK_MIDWORD_MS = 1800   // …and while still mid-word
export const HOVER_CLOSE_MS = 250      // hover-intent grace before popovers close
export const OVERLAP_PAGE = 50         // overlap pairs revealed per scroll step
export const ACCESS_CHIP_MAX = 3     // chips shown inline before collapsing to “+N more”
