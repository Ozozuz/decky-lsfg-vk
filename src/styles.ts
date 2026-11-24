export const tabStyles = `
  .lsfg-vk-tabs > div > div:first-child {
    background: #0D141C;
    box-shadow: none;
    backdrop-filter: none;
  }

  .lsfg-vk-tabs [role="tabpanel"] {
    padding-left: 8px !important;
    padding-right: 8px !important;
  }

  .lsfg-vk-tabs .lsfg-vk-tab-content {
    /* Leave room for the in-game Quick Access footer. */
    padding-bottom: 96px;
  }

  .lsfg-vk-tabs [role="tablist"] {
    display: flex;
    flex-wrap: nowrap;
    justify-content: center;
  }

  .lsfg-vk-tabs [role="tab"] {
    flex: 0 1 auto;
    min-width: 0;
    box-sizing: border-box;
    padding-left: 6px !important;
    padding-right: 6px !important;
    display: flex !important;
    align-items: center;
    justify-content: center;
  }

  .lsfg-vk-tabs [role="tab"] svg {
    display: block;
    margin: 0;
  }

  /* Steam can animate the track around the panels as well as the tab strip. */
  .lsfg-vk-tabs :has(.lsfg-vk-tab-content),
  .lsfg-vk-tabs .lsfg-vk-tab-content,
  .lsfg-vk-tabs [role="tabpanel"],
  .lsfg-vk-tabs [role="tablist"],
  .lsfg-vk-tabs [role="tablist"] > div,
  .lsfg-vk-tabs [role="tablist"] > div > div,
  .lsfg-vk-tabs [role="tablist"] [role="tab"] {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
    scroll-snap-type: none !important;
  }
`;
