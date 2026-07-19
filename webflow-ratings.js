(function() {
    // Hosted ratings.json path:
    const RATINGS_JSON_URL = "https://raw.githubusercontent.com/adamdimitrov/webflow-data/refs/heads/main/ratings.json";

    function cleanUrl(url) {
        if (!url) return "";
        try {
            const u = new URL(url);
            return (u.origin + u.pathname).replace(/\/$/, "").toLowerCase();
        } catch(e) {
            return url.split('?')[0].replace(/\/$/, "").toLowerCase();
        }
    }

    // Helper to recursively find and update the dateModified field inside a JSON-LD object
    function updateSchemaDates(obj, dateStr) {
        let updated = false;
        if (typeof obj === 'object' && obj !== null) {
            if ('dateModified' in obj) {
                obj['dateModified'] = dateStr;
                updated = true;
            }
            // Recursively search child dictionaries and arrays
            for (let key in obj) {
                if (updateSchemaDates(obj[key], dateStr)) {
                    updated = true;
                }
            }
        }
        return updated;
    }

    // Helper to format raw duration text (e.g. "1h" -> "1 hour", "1.5h" -> "1.5 hours")
    function formatDuration(text) {
        const clean = text.replace("⏳ Duration:", "").replace("⏳", "").replace("Duration:", "").trim().toLowerCase();
        if (clean === "1h" || clean === "1 hour" || clean === "60 min" || clean === "60 minutes" || clean === "60 min.") {
            return "1 hour";
        }
        if (clean === "1.5h" || clean === "1.5 hours" || clean === "90 min" || clean === "90 minutes" || clean === "90 min.") {
            return "1.5 hours";
        }
        if (clean === "2h" || clean === "2 hours" || clean === "120 min") {
            return "2 hours";
        }
        return clean;
    }

    // Helper to dynamically update the price value inside a text block (e.g. "25€ / 1 hour", "10-32€")
    function updatePriceText(oldText, newPrice) {
        // Clean double € typos if present
        const cleanedText = oldText.replace(/€€/g, "€").trim();
        // Regex to find a number preceding a '-' or '€' (e.g. "10-32€" or "25€")
        const match = cleanedText.match(/(\d+)(?:-(\d+))?\s*€/);
        if (match) {
            const oldMin = match[1];
            const oldMax = match[2];
            if (oldMax) {
                // Keep max price in a range, update min price
                return cleanedText.replace(`${oldMin}-${oldMax}€`, `${newPrice}-${oldMax}€`)
                                  .replace(`${oldMin}-${oldMax} €`, `${newPrice}-${oldMax} €`);
            } else {
                // Update single price
                return cleanedText.replace(`${oldMin}€`, `${newPrice}€`)
                                  .replace(`${oldMin} €`, `${newPrice} €`);
            }
        }
        return newPrice + "€";
    }

    async function initRatingsSync() {
        console.log("Initializing Webflow Ratings, Price & Duration Sync...");
        try {
            const response = await fetch(`${RATINGS_JSON_URL}?t=${new Date().getTime()}`);
            if (!response.ok) {
                throw new Error(`Failed to load ratings.json: ${response.statusText}`);
            }
            const ratingsDb = await response.json();
            console.log("Successfully loaded ratings database.");

            // 1. Update the visual Last Updated date and Google Schema dateModified
            const timestamp = ratingsDb["_timestamp"];
            if (timestamp) {
                const syncDate = new Date(timestamp);
                const isoDateStr = syncDate.toISOString().split('T')[0]; // Format: YYYY-MM-DD
                
                // Format date visually (e.g. "Jul 19, 2026")
                const options = { year: 'numeric', month: 'short', day: 'numeric' };
                const formattedDate = syncDate.toLocaleDateString('en-US', options);

                // Update visual text element containing "Last updated:"
                const lastUpdatedElement = Array.from(document.querySelectorAll("p")).find(el => el.textContent.includes("Last updated:"));
                if (lastUpdatedElement) {
                    lastUpdatedElement.innerHTML = `<strong>Last updated:</strong> ${formattedDate}`;
                    console.log(`Updated visual 'Last updated' text to: ${formattedDate}`);
                }

                // Update dateModified inside Google JSON-LD schema
                const schemaScripts = document.querySelectorAll('script[type="application/ld+json"]');
                let schemaUpdated = false;
                schemaScripts.forEach(script => {
                    try {
                        const data = JSON.parse(script.textContent);
                        if (updateSchemaDates(data, isoDateStr)) {
                            script.textContent = JSON.stringify(data, null, 2);
                            schemaUpdated = true;
                        }
                    } catch(e) {}
                });
                
                if (schemaUpdated) {
                    console.log(`Updated Google Schema dateModified to: ${isoDateStr}`);
                }
            }

            // 2. Find all cumulative rating cells on the page (matching any grid-cell class containing .star-ratings-2)
            const ratingCells = [];
            document.querySelectorAll(".star-ratings-2").forEach(starEl => {
                const cell = starEl.closest("[class*='grid-cell-']");
                if (cell && !ratingCells.includes(cell)) {
                    ratingCells.push(cell);
                }
            });
            let updatedRatingsCount = 0;
            let updatedPricesCount = 0;

            ratingCells.forEach((gridCell4) => {
                // Find the parent cruise row (the w-layout-grid container)
                const row = gridCell4.closest(".w-layout-grid");
                if (!row) return;

                const cruiseRatingsList = [];

                // 3. Process individual source review blocks (.reviews-3 or .reviews) inside this cruise row
                const reviewsBlocks = row.querySelectorAll(".reviews-3, .reviews");
                reviewsBlocks.forEach((block) => {
                    const link = block.querySelector("a");
                    if (!link) return;

                    const rawUrl = link.getAttribute("href");
                    const cleaned = cleanUrl(rawUrl);
                    const data = ratingsDb[rawUrl] || ratingsDb[cleaned];

                    if (data) {
                        const parsedRating = parseFloat(data.rating);
                        const parsedReviews = parseInt(data.reviews.replace(/[^0-9]/g, ""), 10);

                        if (!isNaN(parsedRating) && !isNaN(parsedReviews)) {
                            // Check if the rating scale is out of 10 (Booking / Hostelworld) and normalize to 5 stars for cumulative weighting
                            const isOutOf10 = rawUrl.includes("booking.com") || rawUrl.includes("hostelworld.com") || cleaned.includes("booking.com") || cleaned.includes("hostelworld.com");
                            
                            cruiseRatingsList.push({
                                rating: isOutOf10 ? (parsedRating / 2) : parsedRating,
                                reviews: parsedReviews
                            });

                            // Update individual review count and rating text
                            const spans = block.querySelectorAll("span");
                            spans.forEach((span) => {
                                const text = span.textContent;
                                if (text.includes("reviews")) {
                                    if (text.includes("/10")) {
                                        // Booking/Hostelworld format: "8.8/10 +4900 reviews"
                                        span.textContent = `${parsedRating.toFixed(1)}/10 +${parsedReviews.toLocaleString()} reviews `;
                                    } else {
                                        // Standard format: "+4900 reviews"
                                        span.textContent = `+${parsedReviews.toLocaleString()} reviews `;
                                    }
                                }
                            });

                            // Update individual rating text (e.g., "4.8")
                            const ratingContainer = block.querySelector(".price-2-copy");
                            if (ratingContainer) {
                                const ratingSpan = ratingContainer.querySelector("span") || ratingContainer;
                                ratingSpan.textContent = parsedRating.toFixed(1);
                            }
                            
                            updatedRatingsCount++;
                        }

                    } else {
                        // No data in ratings.json — parse existing DOM values for accurate cumulative totals
                        const spans = block.querySelectorAll("span");
                        let existingReviews = 0;
                        spans.forEach((span) => {
                            const reviewMatch = span.textContent.match(/\+?([\d,]+)\s*reviews/);
                            if (reviewMatch) {
                                existingReviews = parseInt(reviewMatch[1].replace(/,/g, ""), 10);
                            }
                        });
                        const ratingContainer = block.querySelector(".price-2-copy");
                        let existingRating = 0;
                        if (ratingContainer) {
                            existingRating = parseFloat((ratingContainer.querySelector("span") || ratingContainer).textContent) || 0;
                        }
                        if (existingReviews > 0 && existingRating > 0) {
                            const isOutOf10 = rawUrl.includes("booking.com") || rawUrl.includes("hostelworld.com");
                            cruiseRatingsList.push({
                                rating: isOutOf10 ? (existingRating / 2) : existingRating,
                                reviews: existingReviews
                            });
                        }
                    }
                });

                // 4. Compute and update cumulative stats for this cruise
                if (cruiseRatingsList.length > 0) {
                    let totalReviews = 0;
                    let weightedRatingSum = 0;

                    cruiseRatingsList.forEach((item) => {
                        totalReviews += item.reviews;
                        weightedRatingSum += (item.rating * item.reviews);
                    });

                    const weightedRating = totalReviews > 0 ? (weightedRatingSum / totalReviews) : 0;

                    // Update cumulative rating in the main comparison row (.grid-cell-4 -> .price-2-copy)
                    const cumulativeRatingContainer = gridCell4.querySelector(".price-2-copy");
                    if (cumulativeRatingContainer) {
                        const ratingSpan = cumulativeRatingContainer.querySelector("span") || cumulativeRatingContainer;
                        ratingSpan.textContent = weightedRating.toFixed(1);
                    }

                    // Update cumulative review count in the main comparison row (.grid-cell-4 -> .price-6)
                    const cumulativeReviewsContainer = gridCell4.querySelector(".price-6");
                    if (cumulativeReviewsContainer) {
                        const reviewsSpan = cumulativeReviewsContainer.querySelector("span") || cumulativeReviewsContainer;
                        reviewsSpan.textContent = `+${totalReviews.toLocaleString()} reviews`;
                    }
                }

                // Note: Prices are NOT auto-updated. Scraped "from" prices from GYG/Viator
                // are often promotional minimums that don't match actual ticket prices.
                // Prices should be maintained manually in Webflow for accuracy.
            });

            console.log(`Sync complete! Updated ${updatedRatingsCount} ratings and ${updatedPricesCount} prices (with durations).`);
        } catch (error) {
            console.error("Ratings & Price Sync Error:", error);
        }
    }

    // Run on DOM load
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initRatingsSync);
    } else {
        initRatingsSync();
    }
})();
