# Cross Country Data Analysis

This project is a Python-based data pipeline and Streamlit dashboard for analyzing youth cross country race results. It parses race data from saved HTML files, cleans and merges it, and provides an interactive dashboard for visualizing athlete performance, team scores, and season-over-season trends.

## Features

- **Data Pipeline**: Parses raw HTML files from multiple seasons, standardizes columns, and handles various data formats.
- **Data Cleaning**: Merges data from all races and seasons into a single, clean dataset.
- **Cross Country Scoring**: Correctly calculates team scores by summing the places of the top 5 runners for each team in each race.
- **Interactive Dashboard**: A Streamlit application for exploring the data.
- **Multi-Season Analysis**: Filter data by season, division, and gender to track long-term trends.
- **Visualizations**:
    - Team performance rankings.
    - Individual athlete time and pace progression.
    - Distribution of runners across teams.

## How to Run

1.  **Setup**:
    - Clone the repository.
    - Install dependencies: `pip install pandas streamlit beautifulsoup4`
2.  **Data**:
    - Place raw HTML race result files in the `data/pages` directory. The parser expects filenames in a format like `Meet 1 Boys 3rd-4th Grade 2025.htm`.
3.  **Run Parser**:
    - Execute the script to parse the HTML files and generate the final dataset.
    ```bash
    python run_parser.py
    ```
4.  **Launch Dashboard**:
    - Start the Streamlit application.
    ```bash
    streamlit run dashboard.py
    ```

## Future Development Ideas

Based on our analysis, here are some potential features and enhancements for the future:

- **Predictive Modeling**:
    - Forecast future race times for individual runners based on their historical performance and progression.
    - Develop a model to identify potential "breakout" runners who are improving rapidly.
- **Advanced Analytics**:
    - Incorporate external data like weather conditions or course elevation profiles to analyze their impact on race times.
    - Cluster runners into performance tiers (e.g., elite, competitive, developmental) using machine learning.
    - Create a "what-if" scenario builder to see how adding or removing a runner would affect a team's score.
- **Enhanced Visualizations**:
    - A head-to-head tool to directly compare the race history and stats of two or more athletes.
    - Geospatial mapping to visualize the geographic distribution of runners and teams.
- **Data Pipeline Automation**:
    - Re-implement a fully automated web scraper to fetch new race results as they are posted, removing the need for manual HTML file saving.