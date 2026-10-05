# Problem Statement Review 1

## Viral Product Intelligence: Early Detection of Consumer Demand Surges

---

### The Problem

In India's smartphone market, certain products experience sudden **viral demand surges** —
driven by influencer reviews, social media buzz, and organic consumer interest. By the time
businesses recognise these surges through traditional sales data, it is already **too late** for:

- **Inventory restocking** → stockouts and lost revenue
- **Marketing alignment** → missed campaign windows
- **Supply chain preparation** → delayed fulfilment
- **Competitive pricing** → margin erosion

### The Opportunity

Early consumer signals appear **days or weeks before** a visible demand surge:

| Signal | Source | Lead Time |
|--------|--------|-----------|
| Search interest spike | Google Trends | 7–14 days |
| Review video surge | YouTube Data API | 5–10 days |
| Sentiment shift | YouTube comments | 3–7 days |
| Content engagement spike | YouTube views/likes | 5–12 days |

### Our Approach

1. **Multi-source signal collection** - Google Trends (search interest) + YouTube Data API
   (review videos, comments, engagement)
2. **60-product registry** - RISING (24), STABLE (24), DECLINING (12) smartphones in the India
   market
3. **Pilot validated** - 10 products collected on 2026-10-03; 5,639 comments and 600 video
   records processed
4. **Automated pipeline** - reproducible, idempotent cleaning and alignment scripts

### Expected Outcome

A **Viral Product Radar** system that classifies products as:

🔴 **Surging** | 🟠 **Emerging** | 🟢 **Stable** | 🔵 **Declining**

…with explanations of *which signals* are driving the classification.

### Business Value

| Stakeholder | Benefit |
|------------|---------|
| **Retailers** | Early inventory planning — stock up 7–14 days before surge |
| **Marketers** | Optimal campaign timing — ride the organic wave |
| **Manufacturers** | Demand-driven production scheduling |
| **Analysts** | Data-driven trend forecasting with explainable signals |
