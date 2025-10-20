# 🚀 Quick Start Guide

## Launch the Dashboard

```bash
cd c:\Dev\run_data_scraper
python -m streamlit run dashboard.py
```

The dashboard will open automatically at: **http://localhost:8502**

## Example Use Cases

### 1️⃣ **Track Your Child: "Corbin Summers"**

**What to do:**
1. In the sidebar, select "Corbin Summers" from the "Search Athlete" dropdown
2. Look at the key metrics at the top

**What you'll see:**
- **Team**: St Bernadette
- **Grade**: 4
- **Best Time**: 9:07.79 (547.79 seconds)
- **Best Place**: #4

**Progress Charts:**
- **Time Chart**: Shows he improved from 9:19.00 (Meet 1) to 9:07.79 (Meet 2) - **11.2 seconds faster!** ⬇️
- **Placement Chart**: Improved from 3rd place to 4th place

### 2️⃣ **Track Another Star: "Colton Laurent"**

**What to do:**
1. Select "Colton Laurent" from dropdown

**What you'll see:**
- **Team**: St John the Evangelist  
- **Best Time**: 9:08.99 (548.99 seconds)
- **Huge Improvement**: 9:33.72 (Meet 1) → 9:08.99 (Meet 2) = **24.7 seconds faster!** 🔥

### 3️⃣ **View Team Performance: "St Agnes"**

**What to do:**
1. Keep "All Athletes" selected
2. Select "St Agnes" from "Filter by Team"

**What you'll see:**
- All 22 St Agnes athlete results
- Team averages and statistics
- Fastest athletes from the team

### 4️⃣ **Find Season's Fastest Runners**

**What to do:**
1. Select "All Athletes"
2. Keep "All Teams" selected
3. Scroll to "Fastest Times" section

**What you'll see:**
- Tommy Volinsky: 9:01.42 (fastest overall!)
- Christopher Briczinski: 9:01.65
- Top 10 fastest times of the season

### 5️⃣ **Most Improved Athletes**

**What to do:**
1. Select "All Athletes"
2. Scroll to "Most Improved Athletes"

**What you'll see:**
- Athletes with biggest time drops
- How much they improved (in seconds)
- Their first vs. latest times

## 🎯 Dashboard Tips

### Navigation
- **Sidebar (left)**: All filters and search
- **Main area (right)**: Charts and data

### Interactive Charts
- **Hover**: See exact values
- **Zoom**: Click and drag on chart
- **Reset**: Double-click chart

### Filters Work Together
- Select athlete + team to verify match
- Select multiple grades to compare
- Select specific meets to focus analysis

### Refresh Data
- After running scraper, click "⋮" menu → "Rerun"
- Or just refresh your browser (F5)

## 📊 Understanding the Data

### Time Format
- **Displayed**: 9:01.65 (9 minutes, 1.65 seconds)
- **For calculations**: 541.65 seconds
- **Lower is better** ⬇️

### Placement
- **#1** = First place (winner)
- **Lower numbers** = Better placement
- **Overall place** = Position among all runners

### Meet Numbers
- **Meet 1**: NVJCYO Cross Country Developmental Meet 1
- **Meet 2**: NVJCYO Cross Country Developmental Meet 2
- More meets added as season progresses

## 🔄 Common Workflows

### Weekly Parent Check-In
1. Run scraper after new meet
2. Open dashboard
3. Search for your child
4. Take screenshot of progress chart
5. Share with family! 📱

### Coach Team Review
1. Filter by your team
2. Review "Most Improved"
3. Check team performance chart
4. Plan next practice focus areas

### Pre-Meet Analysis
1. Look at historical times
2. Check typical placements
3. Set realistic goals
4. Review improvement trends

## 🎨 Visual Guide

```
┌─────────────────────────────────────────────────────────┐
│  🏃 Cross Country Performance Dashboard                 │
│  NVJCYO Cross Country - Track athlete performance      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐             │
│  │ Team │  │Grade │  │ Best │  │ Best │             │
│  │      │  │      │  │ Time │  │Place │             │
│  └──────┘  └──────┘  └──────┘  └──────┘             │
│                                                         │
│  ⏱️  Time Progress Across Meets                        │
│  ┌─────────────────────────────────────────┐          │
│  │        📉 Line Chart                     │          │
│  │    (Shows if getting faster)            │          │
│  └─────────────────────────────────────────┘          │
│                                                         │
│  🏆 Placement Progress                                  │
│  ┌─────────────────────────────────────────┐          │
│  │        📊 Line Chart                     │          │
│  │    (Shows placement changes)            │          │
│  └─────────────────────────────────────────┘          │
│                                                         │
│  📋 Meet-by-Meet Results                                │
│  ┌─────────────────────────────────────────┐          │
│  │ Meet  │ # │ Place │ Time │ Pace │ Bib  │          │
│  │ Meet 1│ 1 │   3   │9:19  │ 7:29 │ 459  │          │
│  │ Meet 2│ 2 │   4   │9:07  │ 7:21 │ 459  │          │
│  └─────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────┘
```

## 🎉 Success Stories to Look For

Watch for these positive trends:
- ✅ **Downward time charts** = Getting faster
- ✅ **Lower placement numbers** = Better finishes  
- ✅ **Positive improvements** = Time drops
- ✅ **Consistent paces** = Good racing strategy
- ✅ **Multiple meets** = Building experience

## 📱 Share Results

### Screenshot Tips
1. Select your athlete
2. Use Windows Snip Tool (Win + Shift + S)
3. Capture the chart you want
4. Share via text/email

### Create Reports
1. View athlete profile
2. Take multiple screenshots
3. Combine in Word/PowerPoint
4. Print or share digitally

---

**Happy Tracking! 🏃‍♀️🏃‍♂️**