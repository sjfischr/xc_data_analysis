# 🏃 Cross Country Performance Dashboard

A beautiful, interactive dashboard for tracking athlete performance across cross country meets.

## 🎯 Features

### 🔍 **Search & Filter**
- **Athlete Search**: Find any athlete by name with dropdown selection
- **Team Filter**: View results by school/team
- **Grade Filter**: Filter by grade level (3rd, 4th, etc.)
- **Meet Filter**: Select specific meets to analyze

### 📊 **Individual Athlete View**
When you select an athlete, you'll see:
- **Key Metrics**: Team, grade, best time, and best placement
- **Time Progress Chart**: Interactive line chart showing finish times across meets
- **Placement Progress Chart**: Track overall placement improvements
- **Detailed Results Table**: Complete meet-by-meet breakdown

### 📈 **Season Overview**
When viewing all athletes, you'll see:
- **Summary Statistics**: Total athletes, meets, teams, and results
- **Fastest Times**: Top 10 fastest times across all meets
- **Top Placements**: Athletes who placed in top 10
- **Most Improved**: Athletes with the biggest time improvements
- **Team Performance**: Average times by team with visual charts

## 🚀 Quick Start

### Installation

1. **Install dependencies**:
```bash
pip install streamlit plotly pandas
```

2. **Run the dashboard**:
```bash
cd c:\Dev\run_data_scraper
python -m streamlit run dashboard.py
```

3. **Open in browser**: The dashboard will automatically open at `http://localhost:8502`

## 📁 Data Structure

The dashboard reads from `data/merged/season_results.csv` which includes:
- **Athlete Info**: Name, bib number, grade, team
- **Performance Data**: Finish time, pace, overall placement
- **Meet Info**: Meet name, meet number, meet series
- **Timestamps**: When data was scraped

## 💡 Usage Examples

### Example 1: Track Your Child's Progress
1. Select your child's name from the "Search Athlete" dropdown
2. View their time progression chart to see improvements
3. Check their placement progress to see competitive standings
4. Review the detailed table for meet-by-meet analysis

### Example 2: Compare Team Performance
1. Select your team from "Filter by Team"
2. View the season overview to see team rankings
3. Check the "Team Performance" chart at the bottom
4. See which teammates are in the "Most Improved" list

### Example 3: Find Top Performers
1. Keep "All Athletes" selected
2. Scroll to "Fastest Times" to see the season's best
3. Check "Top Placements" for podium finishers
4. Review "Most Improved" to see who's making gains

## 📊 Key Insights

### Understanding the Charts

**Time Progress Chart (Individual View)**:
- **Downward trend** = Getting faster (good!)
- **Upward trend** = Times are slower
- **Flat line** = Consistent performance

**Placement Progress Chart (Individual View)**:
- **Downward trend** = Better placements (lower numbers are better!)
- **Upward trend** = Lower placements
- Chart is inverted so down is good

**Most Improved Table**:
- Shows time difference between first and last meet
- **Positive numbers** = Got faster
- **Negative numbers** = Got slower

## 🎨 Dashboard Sections

### Sidebar (Left)
- **Filters**: All search and filter controls
- **Data Info**: Last updated timestamp and total results

### Main Area (Right)
- **Header**: Title and meet series information
- **Metrics Row**: Key statistics displayed as cards
- **Charts**: Interactive visualizations (Plotly-powered)
- **Tables**: Detailed data with sortable columns

## 🔄 Updating Data

To add new meet results:

1. **Add URLs** to `urls.txt`:
```txt
https://runsignup.com/race/results/?raceId=XXXXX#resultSetId-YYYYY;perpage:5000
```

2. **Run the scraper**:
```bash
python run_data_scraper.py --control-file urls.txt
```

3. **Refresh the dashboard**: Streamlit will automatically detect changes or click "Rerun" in the browser

## 🛠️ Customization

### Change Dashboard Theme
Edit `.streamlit/config.toml`:
```toml
[theme]
primaryColor = "#1f77b4"
backgroundColor = "#ffffff"
secondaryBackgroundColor = "#f0f2f6"
textColor = "#262730"
```

### Modify Filters
Edit `dashboard.py` to add/remove filters or change default selections.

### Add New Charts
Use Plotly Express or Graph Objects to create new visualizations:
```python
import plotly.express as px
fig = px.line(df, x='meet_number', y='finish_time_s')
st.plotly_chart(fig)
```

## 📱 Mobile Friendly

The dashboard is responsive and works on:
- 💻 Desktop computers
- 📱 Tablets
- 📱 Mobile phones (portrait and landscape)

## 🎯 Parent Dashboard Goals

This dashboard was designed to help parents:
1. ✅ **Track progress**: See how their child improves over the season
2. ✅ **Celebrate wins**: Identify personal records and improvements
3. ✅ **Understand performance**: Visualize trends across multiple meets
4. ✅ **Compare fairly**: See results within proper age/grade divisions
5. ✅ **Stay informed**: Easy-to-understand charts and metrics

## 🐛 Troubleshooting

**Dashboard won't start?**
- Ensure all dependencies are installed: `pip install streamlit plotly pandas`
- Check that `data/merged/season_results.csv` exists

**No data showing?**
- Run the scraper first to generate data
- Check file path matches: `data/merged/season_results.csv`

**Charts not interactive?**
- Make sure you're using a modern browser (Chrome, Firefox, Safari, Edge)
- Try refreshing the page

**Filter not working?**
- Check that the data has values for that field
- Try clearing other filters first

## 📝 Future Enhancements

Potential features to add:
- 📧 Email reports for specific athletes
- 📊 Export individual athlete PDFs
- 🏆 Season leaderboards and awards
- 📈 Predictive analysis (projected times)
- 👥 Head-to-head athlete comparisons
- 🎯 Goal setting and tracking
- 📅 Race calendar integration

## 🤝 Contributing

To improve the dashboard:
1. Edit `dashboard.py`
2. Test changes locally
3. Submit improvements

## 📄 License

This dashboard was created for NVJCYO Cross Country parent tracking.

## 🙏 Acknowledgments

- **Streamlit**: For the amazing dashboard framework
- **Plotly**: For beautiful interactive charts
- **Pandas**: For data processing
- **RunSignup**: For hosting race results

---

**Need Help?** Check the Streamlit docs: https://docs.streamlit.io/

**Last Updated**: October 2025