const hodPresentationMonthStart = (() => {
    const today = frappe.datetime.get_today();
    return `${today.slice(0, 7)}-01`;
})();

frappe.query_reports["HOD Presentation"] = {
    filters: [
        {
            fieldname: "start_date",
            label: __("Start Date"),
            fieldtype: "Date",
            reqd: 1,
            default: hodPresentationMonthStart
        },
        {
            fieldname: "end_date",
            label: __("End Date"),
            fieldtype: "Date",
            reqd: 1,
            default: frappe.datetime.get_today()
        },
        {
            fieldname: "site",
            label: __("Sites"),
            fieldtype: "MultiSelectList",
            reqd: 1,
            get_data: function (txt) {
                return frappe.db.get_link_options("Location", txt);
            }
        },
        {
            fieldname: "summary_type",
            label: __("Summary Type"),
            fieldtype: "Select",
            options: [
                "Daily Summary",
                "Average Per Machine",
                "Weekly Summary",
                "Monthly Summary"
            ],
            reqd: 1,
            default: "Average Per Machine"
        },
        {
            fieldname: "machine_scope",
            label: __("Machine Scope"),
            fieldtype: "Select",
            options: [
                "Production Machines",
                "Swing/Spare Machines",
                "Include Swing/Spare"
            ],
            reqd: 1,
            default: "Include Swing/Spare"
        },
        {
            fieldname: "au_target_filter",
            label: __("A & U Target"),
            fieldtype: "Select",
            options: [
                "100% A & U",
                "85% A & U"
            ],
            reqd: 1,
            default: "85% A & U"
        }
    ],

    onload(report) {
        injectHodPresentationStyles();
        bindHodDashboardTabs(report);
        ensureHodInteractiveReport();

        report.page.add_inner_button(__("Download Presentation"), () => {
            downloadHodPresentation(report);
        });
    },

    after_refresh(report) {
        window.requestAnimationFrame(() => {
            renderHodPresentationLayout(report);
        });
    },

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        const varianceFields = new Set([
            "forecast_variance_bcm",
            "waste_variance_bcm",
            "coal_variance_tons"
        ]);

        if (varianceFields.has(column.fieldname)) {
            const numericValue = Number(data[column.fieldname] || 0);
            const className = numericValue >= 0 ? "hod-positive" : "hod-negative";
            return `<span class="${className}">${value}</span>`;
        }

        if (column.fieldname === "average_bcm_h") {
            const averageBcmH = Number(data.average_bcm_h || 0);

            const className =
                averageBcmH >= 220
                    ? "hod-kpi-good"
                    : averageBcmH >= 200
                        ? "hod-kpi-warning"
                        : "hod-kpi-bad";

            return `<span class="${className}">${value}</span>`;
        }

        if (column.fieldname === "average_availability") {
            const className = Number(data.average_availability || 0) >= 85
                ? "hod-au-good"
                : "hod-au-bad";
            return `<span class="${className}">${value}</span>`;
        }

        if (column.fieldname === "average_utilisation") {
            const className = Number(data.average_utilisation || 0) >= 80
                ? "hod-au-good"
                : "hod-au-bad";
            return `<span class="${className}">${value}</span>`;
        }

        if (["actual_bcm", "total_excavator_hours"].includes(column.fieldname)) {
            return `<span class="hod-kpi-primary">${value}</span>`;
        }

        if (column.fieldname === "excluded_hour_entries") {
            const className = Number(data.excluded_hour_entries || 0) > 0
                ? "hod-warning"
                : "hod-muted";
            return `<span class="${className}">${value}</span>`;
        }

        return value;
    }
};

function ensureHodInteractiveReport() {
    frappe.call({
        method:
            "is_production.production.report." +
            "hod_presentation.hod_presentation." +
            "ensure_interactive_report",
        freeze: false
    }).then(response => {
        if (!response.message) {
            return;
        }

        frappe.show_alert({
            message: __(
                "HOD Presentation switched to live report mode."
            ),
            indicator: "green"
        });

        window.setTimeout(() => {
            window.location.reload();
        }, 500);
    }).catch(error => {
        console.warn(
            "Could not verify HOD report mode.",
            error
        );
    });
}


function bindHodDashboardTabs(report) {
    if (!report?.page?.main) {
        return;
    }

    report.page.main
        .off(
            "click.hodDashboardTabs",
            ".daily-dashboard-tab-button"
        )
        .on(
            "click.hodDashboardTabs",
            ".daily-dashboard-tab-button",
            function () {
                const button = $(this);
                const dashboard = button.closest(
                    ".hod-browser-au-dashboard-copy"
                );
                const selectedTab =
                    button.attr("data-tab");

                dashboard
                    .find(".daily-dashboard-tab-button")
                    .removeClass("btn-primary")
                    .addClass("btn-default");

                button
                    .removeClass("btn-default")
                    .addClass("btn-primary");

                dashboard
                    .find(".daily-dashboard-tab-panel")
                    .hide();

                dashboard
                    .find(
                        `.daily-dashboard-tab-panel[data-panel="${selectedTab}"]`
                    )
                    .show();
            }
        );
}


function normaliseHodSites(value) {
    if (Array.isArray(value)) {
        return value
            .map(item => {
                if (typeof item === "object" && item !== null) {
                    return item.value || item.name || "";
                }

                return String(item || "");
            })
            .map(item => item.trim())
            .filter(Boolean);
    }

    if (!value) {
        return [];
    }

    if (typeof value === "string") {
        const trimmed = value.trim();

        if (!trimmed) {
            return [];
        }

        if (trimmed.startsWith("[")) {
            try {
                const parsed = JSON.parse(trimmed);

                if (Array.isArray(parsed)) {
                    return parsed
                        .map(item => {
                            if (typeof item === "object" && item !== null) {
                                return item.value || item.name || "";
                            }

                            return String(item || "");
                        })
                        .map(item => item.trim())
                        .filter(Boolean);
                }
            } catch (error) {
                console.warn("Could not parse selected HOD sites.", error);
            }
        }

        return trimmed
            .split(",")
            .map(item => item.trim())
            .filter(Boolean);
    }

    return [String(value).trim()].filter(Boolean);
}



async function downloadHodPresentation(report) {
    const getFilterValue = fieldname => {
        if (
            report &&
            typeof report.get_filter_value === "function"
        ) {
            return report.get_filter_value(fieldname);
        }

        return frappe.query_report.get_filter_value(
            fieldname
        );
    };

    const selectedSites = normaliseHodSites(
        getFilterValue("site")
    );

    const startDate = getFilterValue("start_date");
    const endDate = getFilterValue("end_date");

    if (
        !startDate ||
        !endDate ||
        !selectedSites.length
    ) {
        frappe.msgprint({
            title: __("Missing Filters"),
            indicator: "orange",
            message: __(
                "Complete all required filters before downloading."
            )
        });

        return;
    }

    if (startDate > endDate) {
        frappe.msgprint({
            title: __("Invalid Date Range"),
            indicator: "red",
            message: __(
                "Start Date cannot be after End Date."
            )
        });

        return;
    }

    try {
        frappe.dom.freeze(
            __("Generating presentation...")
        );

        await frappe.require(
            "/assets/is_production/js/html-to-image.js"
        );

        if (
            !window.htmlToImage ||
            typeof window.htmlToImage.toJpeg !== "function"
        ) {
            throw new Error(
                __(
                    "The presentation capture library did not load."
                )
            );
        }

        const $layout = report.page.main.find(
            ".hod-presentation-layout"
        );

        if (!$layout.length) {
            throw new Error(
                __(
                    "The presentation layout was not found."
                )
            );
        }

        const layout = $layout[0];

        await waitForHodDashboards(
            layout
        );

        const capturedSlides = [];

        const productionSection =
            layout.querySelector(
                ".hod-browser-section:first-child"
            );

        if (productionSection) {
            capturedSlides.push({
                title:
                    "HOD Production Summary - " +
                    selectedSites.join(" / "),
                image_data: await captureHodSection(
                    productionSection
                )
            });
        }

        const auSites = layout.querySelectorAll(
            ".hod-browser-au-site"
        );

        for (
            let index = 0;
            index < auSites.length;
            index += 1
        ) {
            const siteName =
                selectedSites[index] ||
                `Site ${index + 1}`;

            const auPanel =
                auSites[index].querySelector(
                    '.daily-dashboard-tab-panel[data-panel="au"]'
                );

            for (
                const categorySection
                of getHodAuCategorySections(auPanel)
            ) {
                capturedSlides.push({
                    title:
                        categorySection.title +
                        " - " +
                        siteName,
                    image_data:
                        await captureHodCategorySection(
                            categorySection.element,
                            auPanel
                        )
                });
            }

            const hoursPanel =
                auSites[index].querySelector(
                    '.daily-dashboard-tab-panel[data-panel="hours"]'
                );

            for (
                const categorySection
                of getHodHoursCategorySections(hoursPanel)
            ) {
                capturedSlides.push({
                    title:
                        categorySection.category +
                        " Hours Based Performance - " +
                        siteName,
                    image_data:
                        await captureHodCategorySection(
                            categorySection.element,
                            hoursPanel
                        )
                });
            }
        }

        if (!capturedSlides.length) {
            throw new Error(
                __(
                    "No report sections were available to capture."
                )
            );
        }

        const response = await frappe.call({
            method:
                "is_production.production.report." +
                "hod_presentation.hod_presentation." +
                "download_captured_presentation",
            args: {
                captured_slides:
                    JSON.stringify(capturedSlides),
                site: selectedSites.join(" / "),
                start_date: startDate,
                end_date: endDate,
                period_label:
                    `${startDate} to ${endDate}`
            },
            freeze: false
        });

        const result = response.message || {};

        downloadHodBase64File(
            result.content,
            result.filename
        );
    } catch (error) {
        console.error(
            "HOD presentation generation failed.",
            error
        );

        frappe.msgprint({
            title: __("Presentation Error"),
            indicator: "red",
            message:
                error?.message ||
                __(
                    "The presentation could not be generated."
                )
        });
    } finally {
        frappe.dom.unfreeze();
    }
}


async function waitForHodDashboards(
    layout
) {
    const maximumChecks = 120;

    for (
        let check = 0;
        check < maximumChecks;
        check += 1
    ) {
        const loadingItems =
            layout.querySelectorAll(
                ".hod-browser-au-loading"
            );

        if (!loadingItems.length) {
            return;
        }

        await new Promise(resolve => {
            window.setTimeout(
                resolve,
                250
            );
        });
    }

    throw new Error(
        __(
            "Availability and Utilisation graphs are still loading. Please try again."
        )
    );
}


const HOD_PRESENTATION_HOURS_CATEGORIES = [
    "ADT",
    "Dozer",
    "Excavator"
];


function getHodCategoryFromHeading(value) {
    const headingText = String(
        value || ""
    ).trim().toLowerCase();

    return HOD_PRESENTATION_HOURS_CATEGORIES
        .find(category => {
            const categoryText =
                category.toLowerCase();

            return (
                headingText.startsWith(
                    categoryText + " "
                ) ||
                headingText === categoryText
            );
        }) || "";
}


function getHodAuCategorySections(auPanel) {
    if (!auPanel) {
        return [];
    }

    return Array.from(
        auPanel.querySelectorAll(
            ".isd-chart-section"
        )
    )
        .map(element => {
            const title = (
                element.querySelector(
                    ".isd-chart-section-title"
                )?.textContent || ""
            ).trim();

            const category =
                getHodCategoryFromHeading(title);

            return category
                ? { category, title, element }
                : null;
        })
        .filter(Boolean);
}


function getHodHoursCategorySections(hoursPanel) {
    if (!hoursPanel) {
        return [];
    }

    const chartsRoot =
        hoursPanel.firstElementChild ||
        hoursPanel;

    return HOD_PRESENTATION_HOURS_CATEGORIES
        .map(category => {
            const element = Array.from(
                chartsRoot.children || []
            ).find(candidate => {
                const heading =
                    candidate.firstElementChild;

                const headingText = (
                    heading?.textContent || ""
                ).trim().toLowerCase();

                return headingText.startsWith(
                    category.toLowerCase() + " —"
                ) || headingText.startsWith(
                    category.toLowerCase() + " -"
                );
            });

            return element
                ? { category, element }
                : null;
        })
        .filter(Boolean);
}


async function captureHodCategorySection(
    element,
    categoryPanel
) {
    const styledElements = [
        categoryPanel,
        element,
        ...element.querySelectorAll(
            ".frappe-card, .isd-chart, .isd-chart-stack"
        )
    ];

    const originalStyles = styledElements.map(
        node => ({
            node,
            style: node.getAttribute("style")
        })
    );

    const svgWidths = Array.from(
        element.querySelectorAll("svg")
    ).map(svg => {
        return Number(
            svg.getAttribute("width") ||
            svg.viewBox?.baseVal?.width ||
            svg.scrollWidth ||
            0
        );
    });

    const fullWidth = Math.max(
        1180,
        element.scrollWidth,
        ...svgWidths.map(width => width + 90)
    );

    categoryPanel.style.display = "block";
    categoryPanel.style.width = `${fullWidth}px`;
    categoryPanel.style.maxWidth = "none";
    categoryPanel.style.overflow = "visible";

    element.style.width = `${fullWidth}px`;
    element.style.maxWidth = "none";
    element.style.overflow = "visible";

    element.querySelectorAll(
        ".frappe-card, .isd-chart, .isd-chart-stack"
    ).forEach(node => {
        node.style.maxWidth = "none";
        node.style.overflow = "visible";
    });

    await new Promise(resolve => {
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(resolve);
        });
    });

    try {
        return await captureHodSection(
            element,
            {
                quality: 0.92,
                pixelRatio: 1,
                width: fullWidth
            }
        );
    } finally {
        originalStyles.forEach(
            ({ node, style }) => {
                if (style === null) {
                    node.removeAttribute("style");
                } else {
                    node.setAttribute(
                        "style",
                        style
                    );
                }
            }
        );
    }
}


async function captureHodSection(element, options = {}) {
    if (!element) {
        throw new Error(
            __("The report section was not found.")
        );
    }

    const captureWidth = Math.max(
        Number(options.width || 0),
        element.scrollWidth,
        element.offsetWidth,
        element.clientWidth,
        1
    );

    const captureHeight = Math.max(
        element.scrollHeight,
        element.offsetHeight,
        element.clientHeight,
        1
    );

    const capturePromise =
        window.htmlToImage.toJpeg(
            element,
            {
                quality:
                    options.quality || 0.82,
                pixelRatio:
                    options.pixelRatio || 0.75,
                backgroundColor: "#f7f8fa",
                cacheBust: true,
                width: captureWidth,
                height: captureHeight,

                style: {
                    overflow: "visible"
                },

                filter: node => {
                    if (!node) {
                        return true;
                    }

                    const tagName = (
                        node.tagName || ""
                    ).toLowerCase();

                    if (
                        tagName === "script" ||
                        tagName === "iframe" ||
                        tagName === "grammarly-extension"
                    ) {
                        return false;
                    }

                    if (
                        node.hasAttribute &&
                        (
                            node.hasAttribute(
                                "data-gramm"
                            ) ||
                            node.hasAttribute(
                                "data-gramm_editor"
                            ) ||
                            node.hasAttribute(
                                "data-grammarly-shadow-root"
                            )
                        )
                    ) {
                        return false;
                    }

                    const className =
                        typeof node.className === "string"
                            ? node.className
                            : "";

                    if (
                        className.includes(
                            "grammarly"
                        ) ||
                        className.includes(
                            "gr_"
                        )
                    ) {
                        return false;
                    }

                    return true;
                }
            }
        );

    const timeoutPromise =
        new Promise(
            (_, reject) => {
                window.setTimeout(
                    () => {
                        reject(
                            new Error(
                                __(
                                    "Capturing a presentation slide exceeded 45 seconds."
                                )
                            )
                        );
                    },
                    45000
                );
            }
        );

    return Promise.race([
        capturePromise,
        timeoutPromise
    ]);
}


function downloadHodBase64File(

    base64Content,
    filename
) {
    if (!base64Content) {
        throw new Error(
            __("No PowerPoint file was returned.")
        );
    }

    const binary = window.atob(
        base64Content
    );

    const bytes = new Uint8Array(
        binary.length
    );

    for (
        let index = 0;
        index < binary.length;
        index += 1
    ) {
        bytes[index] = binary.charCodeAt(
            index
        );
    }

    const blob = new Blob(
        [bytes],
        {
            type:
                "application/vnd.openxmlformats-" +
                "officedocument.presentationml.presentation"
        }
    );

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download =
        filename ||
        "HOD_Presentation.pptx";

    document.body.appendChild(link);
    link.click();
    link.remove();

    window.setTimeout(() => {
        URL.revokeObjectURL(url);
    }, 1000);
}




function renderHodPresentationLayout(report) {
    if (
        !report ||
        !report.page ||
        !report.page.main
    ) {
        return;
    }

    const rawRows =
        report.raw_data &&
        Array.isArray(report.raw_data.result)
            ? report.raw_data.result
            : [];

    const rows = (
        rawRows.length
            ? rawRows
            : Array.isArray(report.data)
                ? report.data
                : []
    ).filter(row => row && row.site);

    report.page.main
        .find(".hod-presentation-layout")
        .remove();

    if (report.$summary) {
        report.$summary.hide();
    }

    if (report.$report) {
        report.$report.hide();
    }

    if (report.$report_message) {
        report.$report_message.hide();
    }

    if (report.$chart) {
        report.$chart.hide();
    }

    if (!rows.length) {
        return;
    }

    const renderToken = [
        "hod",
        Date.now(),
        Math.random()
            .toString(36)
            .slice(2)
    ].join("-");

    const availabilitySlots = rows
        .map((row, index) => {
            return `
                <div
                    class="hod-browser-au-site"
                    data-site-index="${index}"
                >
                    <div class="hod-browser-au-loading">
                        Loading Availability &amp; Utilisation
                        for ${hodEscape(row.site)}...
                    </div>
                </div>
            `;
        })
        .join("");

    const layout = $(`
        <div
            class="hod-presentation-layout"
            data-render-token="${renderToken}"
        >
            <section class="hod-browser-section">
                <div class="hod-browser-section-header">
                    <div>
                        <div class="hod-browser-section-title">
                            HOD Production Summary
                        </div>

                        <div class="hod-browser-section-subtitle">
                            ${hodEscape(rows[0].period)}
                        </div>
                    </div>

                    <div class="hod-browser-section-badge">
                        ${rows.length}
                        Site${rows.length === 1 ? "" : "s"}
                    </div>
                </div>

                <div class="hod-browser-filter-ribbon">
                    <div class="hod-browser-filter-item">
                        <span>Summary Type</span>
                        <strong>
                            ${hodEscape(
                                rows[0].summary_type
                            )}
                        </strong>
                    </div>

                    <div class="hod-browser-filter-item">
                        <span>Machine Scope</span>
                        <strong>
                            ${hodEscape(
                                rows[0].machine_scope
                            )}
                        </strong>
                    </div>

                    <div class="hod-browser-filter-item">
                        <span>A &amp; U Target</span>
                        <strong>
                            ${hodEscape(
                                rows[0].au_target_filter
                            )}
                        </strong>
                    </div>
                </div>

                <div class="hod-browser-site-grid">
                    ${rows
                        .map(row => hodRenderSiteCard(row))
                        .join("")}
                </div>
            </section>

            <section
                class="
                    hod-browser-section
                    hod-browser-au-section
                "
            >
                <div class="hod-browser-section-header">
                    <div>
                        <div class="hod-browser-section-title">
                            Availability &amp; Utilisation
                        </div>

                        <div class="hod-browser-section-subtitle">
                            ${hodEscape(rows[0].period)}
                        </div>
                    </div>

                    <div class="hod-browser-section-badge">
                        ${rows.length}
                        Site${rows.length === 1 ? "" : "s"}
                    </div>
                </div>

                <div class="hod-browser-au-site-stack">
                    ${availabilitySlots}
                </div>
            </section>
        </div>
    `);

    if (
        report.$summary &&
        report.$summary.length
    ) {
        layout.insertAfter(
            report.$summary
        );
    } else {
        report.page.main.prepend(
            layout
        );
    }

    loadHodAvailabilityDashboards(
        report,
        rows,
        renderToken
    );
}


function filterHodDashboardCategories(
    dashboard
) {
    if (!dashboard) {
        return;
    }

    dashboard.querySelectorAll(
        ".isd-metric"
    ).forEach(element => {
        const category =
            getHodCategoryFromHeading(
                element.querySelector(
                    ".isd-metric-title"
                )?.textContent
            );

        if (!category) {
            element.remove();
        }
    });

    dashboard.querySelectorAll(
        ".isd-chart-section"
    ).forEach(element => {
        const category =
            getHodCategoryFromHeading(
                element.querySelector(
                    ".isd-chart-section-title"
                )?.textContent
            );

        if (!category) {
            element.remove();
        }
    });

    const hoursPanel =
        dashboard.querySelector(
            '.daily-dashboard-tab-panel[data-panel="hours"]'
        );

    if (hoursPanel) {
        const chartsRoot =
            hoursPanel.firstElementChild ||
            hoursPanel;

        Array.from(
            chartsRoot.children || []
        ).forEach(element => {
            if (
                !getHodCategoryFromHeading(
                    element.firstElementChild
                        ?.textContent
                )
            ) {
                element.remove();
            }
        });
    }
}


async function loadHodAvailabilityDashboards(
    report,
    rows,
    renderToken
) {
    const getFilterValue = fieldname => {
        if (
            report &&
            typeof report.get_filter_value === "function"
        ) {
            return report.get_filter_value(
                fieldname
            );
        }

        return frappe.query_report
            .get_filter_value(
                fieldname
            );
    };

    const method =
        "is_production.production.report." +
        "hod_presentation.hod_presentation." +
        "get_availability_dashboard_html";

    const commonArgs = {
        start_date: getFilterValue(
            "start_date"
        ),
        end_date: getFilterValue(
            "end_date"
        ),
        summary_type: getFilterValue(
            "summary_type"
        ),
        machine_scope: getFilterValue(
            "machine_scope"
        ),
        au_target_filter: getFilterValue(
            "au_target_filter"
        )
    };

    const jobs = rows.map(
        async (row, index) => {
            const findTarget = () => {
                const $layout =
                    report.page.main.find(
                        `.hod-presentation-layout[data-render-token="${renderToken}"]`
                    );

                return $layout.find(
                    `.hod-browser-au-site[data-site-index="${index}"]`
                );
            };

            try {
                const response =
                    await frappe.call({
                        method,
                        args: {
                            ...commonArgs,
                            site: row.site
                        },
                        freeze: false
                    });

                const payload =
                    response.message || {};

                const dashboardHtml =
                    payload.html || "";

                const $target =
                    findTarget();

                if (!$target.length) {
                    return;
                }

                if (!dashboardHtml) {
                    $target.html(`
                        <div class="hod-browser-au-error">
                            No Availability &amp; Utilisation
                            dashboard was returned for
                            ${hodEscape(row.site)}.
                        </div>
                    `);

                    return;
                }

                const $dashboard = $(`
                    <div class="hod-browser-au-dashboard-copy"></div>
                `);

                $dashboard.html(
                    dashboardHtml
                );

                filterHodDashboardCategories(
                    $dashboard[0]
                );

                $dashboard.prepend(`
                    <div class="hod-browser-dashboard-site-name">
                        ${hodEscape(row.site)}
                    </div>
                `);

                $target
                    .empty()
                    .append(
                        $dashboard
                    );
            } catch (error) {
                const $target =
                    findTarget();

                if (!$target.length) {
                    return;
                }

                const errorMessage =
                    error?.message ||
                    __(
                        "Unable to load Availability & Utilisation."
                    );

                $target.html(`
                    <div class="hod-browser-au-error">
                        <strong>
                            ${hodEscape(row.site)}
                        </strong>
                        <br>
                        ${hodEscape(errorMessage)}
                    </div>
                `);
            }
        }
    );

    await Promise.all(
        jobs
    );

    window.setTimeout(() => {
        window.dispatchEvent(
            new Event("resize")
        );
    }, 100);
}




function hodRenderSiteCard(row) {
    const forecastClass =
        hodNumber(row.forecast_variance_bcm) >= 0
            ? "positive"
            : "negative";

    const forecastTextClass =
        hodNumber(row.forecast_variance_bcm) >= 0
            ? "hod-browser-positive"
            : "hod-browser-negative";

    const wasteClass =
        hodNumber(row.waste_variance_bcm) >= 0
            ? "hod-browser-positive"
            : "hod-browser-negative";

    const coalClass =
        hodNumber(row.coal_variance_tons) >= 0
            ? "hod-browser-positive"
            : "hod-browser-negative";

    const averageBcmH = hodNumber(row.average_bcm_h);

    const bcmHourClass =
        averageBcmH >= 220
            ? "hod-browser-positive"
            : averageBcmH >= 200
                ? "hod-browser-warning"
                : "hod-browser-negative";

    const productionHoursClass =
        hodNumber(
            row.production_excavator_hours
        ) > 0
            ? "hod-browser-primary"
            : "hod-browser-negative";

    const forecastDeliveryClass =
        hodNumber(row.forecast_delivery_percent) >= 100
            ? "hod-browser-positive"
            : "hod-browser-warning";

    return `
        <article class="hod-browser-site-card">
            <div class="hod-browser-site-header">
                ${hodEscape(row.site)}
            </div>

            <div class="hod-browser-variance-box ${forecastClass}">
                <div class="hod-browser-variance-label">
                    Forecast Variance
                </div>

                <div class="hod-browser-variance-value ${forecastTextClass}">
                    ${hodFormatSigned(
                        row.forecast_variance_bcm,
                        0
                    )} BCM
                </div>
            </div>

            <table class="hod-browser-metric-table">
                <tbody>
                    ${hodMetricRow(
                        "Monthly target",
                        "BCM",
                        hodFormatNumber(
                            row.monthly_target_bcm,
                            0
                        )
                    )}

                    ${hodMetricRow(
                        "Forecast",
                        "BCM",
                        hodFormatNumber(
                            row.forecast_bcm,
                            0
                        )
                    )}

                    ${hodMetricRow(
                        "Waste variance",
                        "BCM",
                        hodFormatSigned(
                            row.waste_variance_bcm,
                            0
                        ),
                        wasteClass
                    )}

                    ${hodMetricRow(
                        "Coal variance",
                        "TONS",
                        hodFormatSigned(
                            row.coal_variance_tons,
                            0
                        ),
                        coalClass
                    )}

                    ${hodMetricRow(
                        "Actual BCMs",
                        "BCM",
                        hodFormatNumber(
                            row.actual_bcm,
                            0
                        ),
                        "hod-browser-primary"
                    )}

                    ${hodMetricRow(
                        "Actual coal",
                        "TONS",
                        hodFormatNumber(
                            row.actual_coal_tons,
                            0
                        )
                    )}

                    ${hodMetricRow(
                        "Daily required",
                        "BCM",
                        hodFormatNumber(
                            row.daily_required_bcm,
                            1
                        )
                    )}

                    ${hodMetricRow(
                        "Daily achieved",
                        "BCM",
                        hodFormatNumber(
                            row.daily_achieved_bcm,
                            1
                        )
                    )}

                    ${hodMetricRow(
                        "Excavator hours (Production Hours)",
                        "HRS",
                        hodFormatNumber(
                            row.production_excavator_hours,
                            1
                        ),
                        productionHoursClass
                    )}

                    ${hodMetricRow(
                        `Average BCM/H (${hodFormatNumber(
                            row.actual_bcm,
                            0
                        )} BCM / ${hodFormatNumber(
                            row.production_excavator_hours,
                            1
                        )} HRS)`,
                        "",
                        hodFormatNumber(
                            row.average_bcm_h,
                            1
                        ),
                        bcmHourClass
                    )}

                    ${hodMetricRow(
                        "Days worked / left",
                        "",
                        `${hodFormatNumber(
                            row.days_worked,
                            0
                        )} / ${hodFormatNumber(
                            row.days_left,
                            0
                        )}`
                    )}

                    ${hodMetricRow(
                        "Strip ratio",
                        "",
                        hodFormatNumber(
                            row.strip_ratio,
                            1
                        )
                    )}
                </tbody>
            </table>

            <div class="hod-browser-forecast-footer">
                Forecast delivery
                <strong class="${forecastDeliveryClass}">
                    ${hodFormatNumber(
                        row.forecast_delivery_percent,
                        1
                    )}%
                </strong>
            </div>
        </article>
    `;
}


function hodMetricRow(
    label,
    unit,
    value,
    className = ""
) {
    return `
        <tr>
            <td class="hod-browser-metric-label">
                ${hodEscape(label)}
            </td>

            <td class="hod-browser-metric-unit ${className}">
                ${hodEscape(unit)}
            </td>

            <td class="hod-browser-metric-value ${className}">
                ${hodEscape(value)}
            </td>
        </tr>
    `;
}


function hodKpiCard(
    label,
    value,
    note,
    className = ""
) {
    return `
        <div class="hod-browser-kpi-card ${className}">
            <div class="hod-browser-kpi-label">
                ${hodEscape(label)}
            </div>

            <div class="hod-browser-kpi-value">
                ${hodEscape(value)}
            </div>

            <div class="hod-browser-kpi-note">
                ${hodEscape(note)}
            </div>
        </div>
    `;
}


function hodNumber(value) {
    const numericValue = Number(
        String(value ?? 0)
            .replace(/,/g, "")
            .replace(/%/g, "")
            .trim()
    );

    return Number.isFinite(numericValue)
        ? numericValue
        : 0;
}


function hodFormatNumber(
    value,
    decimals = 0
) {
    return new Intl.NumberFormat(
        "en-US",
        {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        }
    ).format(
        hodNumber(value)
    );
}


function hodFormatSigned(
    value,
    decimals = 0
) {
    const numericValue = hodNumber(value);

    const sign =
        numericValue >= 0
            ? "+"
            : "-";

    return `${sign}${hodFormatNumber(
        Math.abs(numericValue),
        decimals
    )}`;
}


function hodEscape(value) {
    return frappe.utils.escape_html(
        String(value ?? "")
    );
}


function injectHodPresentationStyles() {
    if (document.getElementById("hod-presentation-report-style")) return;

    const style = document.createElement("style");
    style.id = "hod-presentation-report-style";
    style.textContent = `
        .hod-positive {
            color: #18a957;
            font-weight: 800;
        }

        .hod-negative {
            color: #e03124;
            font-weight: 800;
        }

        .hod-kpi-primary {
            color: #0f1f53;
            font-weight: 900;
        }

        .hod-kpi-good,
        .hod-kpi-warning,
        .hod-kpi-bad,
        .hod-au-good,
        .hod-au-bad {
            display: inline-block;
            min-width: 62px;
            padding: 3px 8px;
            border-radius: 999px;
            font-weight: 900;
            text-align: center;
        }

        .hod-kpi-good,
        .hod-au-good {
            color: #166534;
            background: #dcfce7;
            border: 1px solid #86efac;
        }

        .hod-kpi-warning {
            color: #92400e;
            background: #fef3c7;
            border: 1px solid #fcd34d;
        }

        .hod-kpi-bad,
        .hod-au-bad {
            color: #b91c1c;
            background: #fee2e2;
            border: 1px solid #fca5a5;
        }

        .hod-warning {
            color: #b45309;
            font-weight: 800;
        }

        .hod-muted {
            color: #64748b;
            font-weight: 700;
        }

        .hod-presentation-layout {
            display: flex;
            flex-direction: column;
            gap: 16px;
            margin: 12px 0 28px;
        }

        .hod-browser-section {
            background: #f7f8fa;
            border: 1px solid #cfd6de;
            border-radius: 10px;
            padding: 12px;
            box-shadow: 0 2px 8px rgba(15, 31, 83, 0.06);
        }

        .hod-browser-section-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            background: #0f1f53;
            color: #ffffff;
            border-radius: 7px;
            padding: 10px 14px;
            margin-bottom: 12px;
        }

        .hod-browser-section-title {
            font-size: 17px;
            line-height: 1.2;
            font-weight: 900;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }

        .hod-browser-section-subtitle {
            margin-top: 3px;
            font-size: 11px;
            color: #d9e2ec;
            font-weight: 600;
        }

        .hod-browser-section-badge {
            background: #ffffff;
            color: #0f1f53;
            border-radius: 999px;
            padding: 5px 12px;
            font-size: 11px;
            font-weight: 900;
            white-space: nowrap;
        }

        .hod-browser-production-card {
            background: #ffffff;
            border: 1px solid #d7dde5;
            border-radius: 8px;
            padding: 12px;
        }

        .hod-browser-variance-box {
            border-radius: 7px;
            padding: 14px;
            margin-bottom: 12px;
            text-align: center;
            background: #f5eaea;
        }

        .hod-browser-variance-box.positive {
            background: #dfeee5;
        }

        .hod-browser-variance-box.negative {
            background: #f5eaea;
        }

        .hod-browser-variance-label {
            color: #62708a;
            font-size: 11px;
            line-height: 1.2;
            font-weight: 900;
            text-transform: uppercase;
            margin-bottom: 4px;
        }

        .hod-browser-variance-value {
            font-size: 26px;
            line-height: 1.1;
            font-weight: 900;
        }

        .hod-browser-metric-table {
            width: 100%;
            border-collapse: collapse;
            overflow: hidden;
            border-radius: 7px;
        }

        .hod-browser-metric-table tr:nth-child(even) {
            background: #f0f2f5;
        }

        .hod-browser-metric-table td {
            border: 1px solid #d7dde5;
            padding: 7px 10px;
            font-size: 12px;
            line-height: 1.2;
        }

        .hod-browser-metric-label {
            width: 55%;
            color: #5e6c84;
            font-weight: 600;
        }

        .hod-browser-metric-unit {
            width: 15%;
            color: #304055;
            text-align: right;
            font-weight: 800;
        }

        .hod-browser-metric-value {
            width: 30%;
            color: #304055;
            text-align: right;
            font-weight: 900;
        }

        .hod-browser-kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(160px, 1fr));
            gap: 12px;
        }

        .hod-browser-au-grid {
            grid-template-columns: repeat(3, minmax(180px, 1fr));
        }

        .hod-browser-kpi-card {
            background: #ffffff;
            border: 1px solid #d7dde5;
            border-top: 4px solid #0f1f53;
            border-radius: 8px;
            padding: 14px;
            min-height: 125px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            text-align: center;
        }

        .hod-browser-kpi-card.hod-browser-positive {
            border-top-color: #18a957;
        }

        .hod-browser-kpi-card.hod-browser-negative {
            border-top-color: #e03124;
        }

        .hod-browser-kpi-card.hod-browser-warning {
            border-top-color: #d97706;
        }

        .hod-browser-kpi-card.hod-browser-primary {
            border-top-color: #0f1f53;
        }

        .hod-browser-kpi-label {
            color: #62708a;
            font-size: 10px;
            font-weight: 900;
            text-transform: uppercase;
            margin-bottom: 7px;
        }

        .hod-browser-kpi-value {
            color: #0f1f53;
            font-size: 27px;
            line-height: 1.05;
            font-weight: 900;
        }

        .hod-browser-kpi-card.hod-browser-positive
        .hod-browser-kpi-value {
            color: #18a957;
        }

        .hod-browser-kpi-card.hod-browser-negative
        .hod-browser-kpi-value {
            color: #e03124;
        }

        .hod-browser-kpi-card.hod-browser-warning
        .hod-browser-kpi-value {
            color: #d97706;
        }

        .hod-browser-kpi-note {
            color: #64748b;
            font-size: 10px;
            line-height: 1.25;
            margin-top: 8px;
        }

        .hod-browser-filter-ribbon {
            display: grid;
            grid-template-columns: repeat(3, minmax(180px, 1fr));
            gap: 10px;
            margin-bottom: 12px;
        }

        .hod-browser-filter-item {
            background: #ffffff;
            border: 1px solid #d7dde5;
            border-radius: 7px;
            padding: 9px 11px;
        }

        .hod-browser-filter-item span {
            display: block;
            color: #64748b;
            font-size: 9px;
            font-weight: 900;
            text-transform: uppercase;
            margin-bottom: 3px;
        }

        .hod-browser-filter-item strong {
            display: block;
            color: #102a43;
            font-size: 12px;
            line-height: 1.25;
        }

        .hod-browser-chart-title {
            color: #102a43;
            font-size: 13px;
            font-weight: 900;
            text-transform: uppercase;
            margin: 16px 2px 8px;
        }

        .hod-browser-chart-slot {
            width: 100%;
        }

        .hod-browser-chart {
            display: block !important;
            width: 100%;
            margin: 0 !important;
            padding: 12px;
            background: #ffffff;
            border: 1px solid #d7dde5;
            border-radius: 8px;
        }

        .hod-browser-chart .chart-container {
            margin: 0 !important;
        }

        .hod-browser-empty {
            background: #ffffff;
            border: 1px dashed #cfd6de;
            border-radius: 8px;
            padding: 22px;
            color: #64748b;
            text-align: center;
            font-size: 12px;
        }

        .hod-browser-positive {
            color: #18a957 !important;
        }

        .hod-browser-negative {
            color: #e03124 !important;
        }

        .hod-browser-primary {
            color: #0f1f53 !important;
        }

        .hod-browser-warning {
            color: #d97706 !important;
        }

        .hod-browser-muted {
            color: #64748b !important;
        }

        .hod-browser-site-grid {
            display: grid;
            grid-template-columns: repeat(
                auto-fit,
                minmax(430px, 1fr)
            );
            gap: 12px;
            align-items: start;
        }

        .hod-browser-site-card {
            min-width: 0;
            background: #f7f8fa;
            border: 1px solid #cfd6de;
            border-radius: 9px;
            padding: 10px;
        }

        .hod-browser-site-header {
            background: #0f1f53;
            color: #ffffff;
            text-align: center;
            text-transform: uppercase;
            font-size: 17px;
            line-height: 1.1;
            font-weight: 900;
            letter-spacing: 0.3px;
            border-radius: 6px;
            padding: 9px 12px;
            margin-bottom: 10px;
        }

        .hod-browser-site-footer {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 9px;
        }

        .hod-browser-site-footer span {
            background: #ffffff;
            border: 1px solid #d7dde5;
            border-radius: 999px;
            color: #62708a;
            padding: 4px 8px;
            font-size: 9px;
            font-weight: 800;
        }


        .hod-browser-forecast-footer {
            color: #62708a;
            padding: 8px 4px 1px;
            font-size: 11px;
            line-height: 1.2;
            font-weight: 600;
        }

        .hod-browser-forecast-footer strong {
            margin-left: 3px;
            font-weight: 900;
        }

        .hod-browser-au-section {
            padding: 12px;
        }

        .hod-browser-au-site-stack {
            display: flex;
            flex-direction: column;
            gap: 14px;
        }

        .hod-browser-au-site {
            width: 100%;
            min-width: 0;
            background: #ffffff;
            border: 1px solid #d7dde5;
            border-radius: 9px;
            overflow: hidden;
        }

        .hod-browser-au-loading,
        .hod-browser-au-error {
            min-height: 110px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 22px;
            text-align: center;
            color: #64748b;
            background: #ffffff;
            font-size: 12px;
            font-weight: 800;
        }

        .hod-browser-au-error {
            color: #b91c1c;
            background: #fff7f7;
        }

        .hod-browser-dashboard-site-name {
            padding: 12px 16px;
            background: #0f1f53;
            color: #ffffff;
            border-bottom: 4px solid #e03124;
            font-size: 18px;
            font-weight: 900;
            letter-spacing: 0.4px;
            text-align: center;
            text-transform: uppercase;
        }

        .hod-browser-au-dashboard-copy {
            width: 100%;
            max-width: 100%;
            overflow-x: auto;
            background: #ffffff;
        }

        .hod-browser-au-dashboard-copy
        .eng-dashboard--daily-availability,
        .hod-browser-au-dashboard-copy
        .eng-dashboard,
        .hod-browser-au-dashboard-copy
        .isd-hourly-dashboard {
            width: 100% !important;
            min-width: 1180px;
            margin: 0 !important;
            padding: 0 !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-note {
            display: none !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-site {
            margin: 0 !important;
            border: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-site-title {
            border-radius: 0 !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-chart-stack {
            width: 100% !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-contentrow {
            width: 100% !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-metrics {
            display: flex !important;
            flex-wrap: nowrap !important;
            justify-content: stretch !important;
            align-items: stretch !important;
            gap: 8px !important;
            width: 100% !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-metric {
            flex: 1 1 0 !important;
            width: auto !important;
            min-width: 105px !important;
            max-width: none !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-chart-stack {
            width: 100% !important;
        }

        .hod-browser-au-dashboard-copy
        .isd-chart-section {
            width: 100% !important;
            margin-bottom: 0 !important;
        }

        @media (max-width: 1100px) {
            .hod-browser-site-grid,
            .hod-browser-kpi-grid,
            .hod-browser-au-grid {
                grid-template-columns: repeat(2, minmax(160px, 1fr));
            }
        }

        @media (max-width: 768px) {
            .hod-browser-section-header {
                align-items: flex-start;
                flex-direction: column;
            }

            .hod-browser-site-grid,
            .hod-browser-kpi-grid,
            .hod-browser-au-grid,
            .hod-browser-filter-ribbon {
                grid-template-columns: 1fr;
            }

            .hod-browser-section-badge {
                align-self: flex-start;
            }

            .hod-browser-metric-label {
                width: 48%;
            }

            .hod-browser-metric-unit {
                width: 18%;
            }

            .hod-browser-metric-value {
                width: 34%;
            }
        }

        .hod-downtime-summary {
            margin: 16px;
            padding: 16px;
            background: linear-gradient(135deg, #f8fafc 0%, #eef2f7 100%);
            border: 1px solid #cbd5e1;
            border-radius: 14px;
            box-shadow: 0 6px 18px rgba(15, 31, 83, 0.10);
            overflow: hidden;
        }

        .hod-downtime-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 18px;
            margin: -16px -16px 16px;
            background: linear-gradient(135deg, #0f1f53 0%, #1d3b82 100%);
            color: #ffffff;
            border-bottom: 4px solid #e03124;
        }

        .hod-downtime-title {
            font-size: 18px;
            line-height: 1.2;
            font-weight: 900;
            letter-spacing: 0.4px;
            text-transform: uppercase;
        }

        .hod-downtime-period {
            margin-top: 5px;
            color: #dbeafe;
            font-size: 11px;
            font-weight: 700;
        }

        .hod-downtime-kpis {
            display: grid;
            grid-template-columns: repeat(6, minmax(130px, 1fr));
            gap: 10px;
            margin-bottom: 16px;
        }

        .hod-downtime-kpi {
            position: relative;
            min-height: 92px;
            padding: 13px 10px;
            background: #ffffff;
            border: 1px solid #d7dee8;
            border-radius: 12px;
            box-shadow: 0 3px 10px rgba(15, 31, 83, 0.07);
            text-align: center;
            overflow: hidden;
        }

        .hod-downtime-kpi::before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 5px;
            background: #0f1f53;
        }

        .hod-downtime-kpi span {
            display: block;
            margin-top: 5px;
            margin-bottom: 8px;
            color: #64748b;
            font-size: 9px;
            line-height: 1.2;
            font-weight: 900;
            letter-spacing: 0.4px;
            text-transform: uppercase;
        }

        .hod-downtime-kpi strong {
            display: block;
            color: #102a43;
            font-size: 18px;
            line-height: 1.15;
            font-weight: 900;
            overflow-wrap: anywhere;
        }

        .hod-downtime-open::before {
            background: #e03124;
        }

        .hod-downtime-open strong {
            color: #c81e1e;
        }

        .hod-downtime-closed::before {
            background: #18a957;
        }

        .hod-downtime-closed strong {
            color: #168447;
        }

        .hod-downtime-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            background: #ffffff;
            border: 1px solid #d7dee8;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 3px 10px rgba(15, 31, 83, 0.06);
        }

        .hod-downtime-table thead th {
            padding: 11px 10px;
            background: #0f1f53;
            color: #ffffff;
            border-right: 1px solid rgba(255, 255, 255, 0.18);
            font-size: 10px;
            font-weight: 900;
            letter-spacing: 0.3px;
            text-align: left;
            text-transform: uppercase;
            white-space: nowrap;
        }

        .hod-downtime-table thead th:last-child {
            border-right: none;
        }

        .hod-downtime-table tbody td {
            padding: 11px 10px;
            border-right: 1px solid #e2e8f0;
            border-bottom: 1px solid #e2e8f0;
            color: #334155;
            font-size: 11px;
            line-height: 1.35;
            font-weight: 700;
            vertical-align: middle;
        }

        .hod-downtime-table tbody td:last-child {
            border-right: none;
        }

        .hod-downtime-table tbody tr:last-child td {
            border-bottom: none;
        }

        .hod-downtime-table tbody tr:nth-child(even) {
            background: #f8fafc;
        }

        .hod-downtime-table tbody tr:hover {
            background: #eef4ff;
        }

        .hod-downtime-table tbody td:nth-child(2),
        .hod-downtime-table tbody td:nth-child(3),
        .hod-downtime-table tbody td:nth-child(4),
        .hod-downtime-table tbody td:nth-child(5) {
            text-align: center;
            font-weight: 900;
        }

        .hod-downtime-table tbody td:nth-child(2) {
            color: #b45309;
        }

        .hod-downtime-table tbody td:nth-child(4) {
            color: #c81e1e;
        }

        .hod-downtime-table tbody td:nth-child(5) {
            color: #168447;
        }

        .hod-downtime-empty {
            padding: 24px !important;
            text-align: center !important;
            color: #64748b !important;
            font-style: italic;
        }

        @media (max-width: 1200px) {
            .hod-downtime-kpis {
                grid-template-columns: repeat(3, minmax(150px, 1fr));
            }
        }

        @media (max-width: 768px) {
            .hod-downtime-summary {
                margin: 8px;
                padding: 10px;
            }

            .hod-downtime-header {
                margin: -10px -10px 12px;
                padding: 12px;
            }

            .hod-downtime-kpis {
                grid-template-columns: repeat(2, minmax(130px, 1fr));
            }

            .hod-downtime-table {
                display: block;
                overflow-x: auto;
                white-space: nowrap;
            }
        }

    `;

    document.head.appendChild(style);
}