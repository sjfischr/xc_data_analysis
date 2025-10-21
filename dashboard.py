import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# Page configuration
st.set_page_config(
    page_title="Cross Country Performance Dashboard",
    page_icon="🏃",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
    <style>
    .main {
        padding: 0rem 1rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 0.5rem 0;
    }
    h1 {
        color: #1f77b4;
        padding-bottom: 1rem;
    }
    </style>
    """, unsafe_allow_html=True)

# Load data
@st.cache_data
def load_data():
    try:
        df = pd.read_csv('data/merged/season_results.csv')
        # Ensure season_year is numeric
        if 'season_year' in df.columns:
            df['season_year'] = pd.to_numeric(df['season_year'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"Error loading data: {str(e)}")
        return pd.DataFrame()

try:
    df = load_data()
    
    if df.empty:
        st.error("No data available. Please check the data file.")
        st.stop()
except Exception as e:
    st.error(f"Critical error: {str(e)}")
    st.stop()

# Title and description
st.title("🏃 Cross Country Performance Dashboard")
if 'season_year' in df.columns and not df['season_year'].isna().all():
    seasons = sorted([int(y) for y in df['season_year'].dropna().unique()])
    season_range = f"{min(seasons)}-{max(seasons)}" if len(seasons) > 1 else str(seasons[0])
    st.markdown(f"**NVJCYO Cross Country Developmental Meets** | Seasons: {season_range}")
else:
    st.markdown("**NVJCYO Cross Country Developmental Meets** - Track athlete performance across meets")

# Sidebar filters
st.sidebar.header("🔍 Filters")

# Season filter (NEW!)
if 'season_year' in df.columns and not df['season_year'].isna().all():
    available_seasons = sorted([int(y) for y in df['season_year'].dropna().unique()], reverse=True)
    season_options = [f"{s} {'(Current)' if s == max(available_seasons) else ''}" for s in available_seasons] + ["All Seasons"]
    
    selected_season_display = st.sidebar.selectbox(
        "📅 Season",
        season_options,
        index=0,  # Default to current season
        help="Select a specific season or 'All Seasons' for multi-year analysis"
    )
    
    # Parse selection
    if selected_season_display == "All Seasons":
        selected_season = "All"
        st.sidebar.info("🔓 Multi-year analysis mode enabled")
    else:
        selected_season = int(selected_season_display.split()[0])
        st.sidebar.success(f"📊 Viewing {selected_season} season")
else:
    selected_season = "All"

# Athlete search
athlete_list = sorted(df['athlete_full_name'].dropna().unique())
selected_athlete = st.sidebar.selectbox(
    "Search Athlete",
    ["All Athletes"] + athlete_list,
    help="Select an athlete to view their progress"
)

# Team filter
team_list = sorted(df['team_name'].dropna().unique())
selected_team = st.sidebar.selectbox(
    "Filter by Team",
    ["All Teams"] + team_list
)

# Grade filter
grade_list = sorted(df['grade'].dropna().unique())
selected_grade = st.sidebar.multiselect(
    "Filter by Grade",
    grade_list,
    default=grade_list
)

# Meet filter
meet_list = sorted(df['meet_number'].dropna().unique())
selected_meets = st.sidebar.multiselect(
    "Filter by Meet",
    meet_list,
    default=meet_list
)

# Apply filters
filtered_df = df.copy()

# Apply season filter first
if selected_season != "All" and 'season_year' in df.columns:
    filtered_df = filtered_df[filtered_df['season_year'] == selected_season]

if selected_athlete != "All Athletes":
    filtered_df = filtered_df[filtered_df['athlete_full_name'] == selected_athlete]
if selected_team != "All Teams":
    filtered_df = filtered_df[filtered_df['team_name'] == selected_team]
if selected_grade:
    filtered_df = filtered_df[filtered_df['grade'].isin(selected_grade)]
if selected_meets:
    filtered_df = filtered_df[filtered_df['meet_number'].isin(selected_meets)]

# Main dashboard
if selected_athlete != "All Athletes":
    # Individual athlete view
    st.header(f"📊 {selected_athlete}'s Performance")
    
    # Get all data for this athlete (across all seasons if "All Seasons" selected)
    if selected_season == "All":
        athlete_data = df[df['athlete_full_name'] == selected_athlete].sort_values(['season_year', 'meet_number'])
    else:
        athlete_data = filtered_df.sort_values('meet_number')
    
    if len(athlete_data) > 0:
        # Check if multi-season data exists
        has_multi_season = 'season_year' in athlete_data.columns and athlete_data['season_year'].nunique() > 1
        
        # Key metrics row
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "Team",
                athlete_data.iloc[0]['team_name']
            )
        
        with col2:
            if has_multi_season:
                seasons_competed = athlete_data['season_year'].dropna().unique()
                st.metric(
                    "Seasons",
                    f"{len(seasons_competed)} ({int(min(seasons_competed))}-{int(max(seasons_competed))})"
                )
            else:
                st.metric(
                    "Grade",
                    athlete_data.iloc[0]['grade']
                )
        
        with col3:
            best_time = athlete_data['finish_time_s'].min()
            if pd.notna(best_time):
                st.metric(
                    "Career Best" if has_multi_season else "Best Time",
                    f"{best_time//60:.0f}:{best_time%60:05.2f}"
                )
        
        with col4:
            best_place = athlete_data['place_overall'].min()
            if pd.notna(best_place):
                st.metric(
                    "Best Place",
                    f"#{int(best_place)}"
                )
        
        # Progress chart
        st.subheader("⏱️ Time Progress")
        
        # Create x-axis based on whether multi-season or single season
        if has_multi_season:
            # Create combined label for x-axis
            athlete_data['race_label'] = athlete_data.apply(
                lambda row: f"{int(row['season_year'])} M{int(row['meet_number'])}" 
                if pd.notna(row['season_year']) and pd.notna(row['meet_number']) else '', 
                axis=1
            )
            x_data = athlete_data['race_label']
            x_title = "Season & Meet"
        else:
            x_data = athlete_data['meet_number']
            x_title = "Meet Number"
        
        # Show both raw time and normalized pace
        col_chart1, col_chart2 = st.columns(2)
        
        with col_chart1:
            st.caption("Raw Finish Time (varies by division distance)")
            fig_time = go.Figure()
            fig_time.add_trace(go.Scatter(
                x=x_data,
                y=athlete_data['finish_time_s'],
                mode='lines+markers',
                name='Finish Time',
                line=dict(color='#1f77b4', width=3),
                marker=dict(size=12),
                text=athlete_data['finish_time_str'],
                hovertemplate='<b>%{text}</b><br>%{y:.2f} seconds<extra></extra>'
            ))
            
            fig_time.update_layout(
                xaxis_title=x_title,
                yaxis_title="Time (seconds)",
                hovermode='x unified',
                height=400,
                showlegend=False
            )
            
            st.plotly_chart(fig_time, use_container_width=True)
        
        with col_chart2:
            st.caption("Normalized Pace per Mile (fair comparison)")
            fig_pace = go.Figure()
            fig_pace.add_trace(go.Scatter(
                x=x_data,
                y=athlete_data['pace_per_mi_min'],
                mode='lines+markers',
                name='Pace per Mile',
                line=dict(color='#2ca02c', width=3),
                marker=dict(size=12),
                text=athlete_data['pace_per_mi_str'],
                hovertemplate='<b>%{text}/mile</b><br>%{y:.2f} min/mi<extra></extra>'
            ))
            
            fig_pace.update_layout(
                xaxis_title=x_title,
                yaxis_title="Pace (min/mile)",
                hovermode='x unified',
                height=400,
                showlegend=False
            )
            
            st.plotly_chart(fig_pace, use_container_width=True)
        
        # Placement chart
        st.subheader("🏆 Placement Progress")
        
        fig_place = go.Figure()
        fig_place.add_trace(go.Scatter(
            x=x_data,
            y=athlete_data['place_overall'],
            mode='lines+markers',
            name='Overall Place',
            line=dict(color='#ff7f0e', width=3),
            marker=dict(size=12),
            hovertemplate='<b>Place %{y}</b><extra></extra>'
        ))
        
        fig_place.update_layout(
            xaxis_title=x_title,
            yaxis_title="Overall Place",
            yaxis_autorange='reversed',  # Lower place is better
            hovermode='x unified',
            height=400,
            showlegend=False
        )
        
        st.plotly_chart(fig_place, width='stretch')
        
        # Detailed results table
        st.subheader("📋 Race Results")
        
        display_cols = ['meet_name', 'meet_number', 'place_overall', 'finish_time_str', 'pace_str']
        if has_multi_season:
            display_cols.insert(0, 'season_year')
        
        results_display = athlete_data[display_cols].copy()
        
        # Rename columns for display
        col_names = ['Season', 'Meet', 'Meet #', 'Place', 'Time', 'Pace'] if has_multi_season else ['Meet', 'Meet #', 'Place', 'Time', 'Pace']
        results_display.columns = col_names
        
        st.dataframe(
            results_display,
            hide_index=True,
            use_container_width=True
        )
        
    else:
        st.info("No results found for this athlete with the selected filters.")

else:
    # Overview dashboard
    if selected_season == "All":
        st.header("📈 Multi-Season Overview")
    else:
        st.header(f"📈 {selected_season} Season Overview")
    
    # Add info box about pace normalization
    with st.expander("ℹ️ Why Pace Per Mile?", expanded=False):
        st.markdown("""
        **Races have different distances by division:**
        - 2nd Grade & Frosh: 2km (1.24 miles)
        - JV: 3km (1.86 miles)
        - Varsity: 4km (2.49 miles)
        
        **Using pace per mile lets us:**
        - Compare athletes across different divisions fairly
        - Track true improvement as athletes move up divisions
        - Identify consistently fast runners regardless of race distance
        
        **Example:** A 11:37 Frosh time (2km) = 5:48/mile pace  
        vs. a 16:50 JV time (3km) = 5:36/mile pace → **Actually faster!**
        """)
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            "Total Athletes",
            len(filtered_df['athlete_full_name'].unique())
        )
    
    with col2:
        if selected_season == "All":
            st.metric(
                "Seasons",
                len(filtered_df['season_year'].dropna().unique()) if 'season_year' in filtered_df.columns else 1
            )
        else:
            st.metric(
                "Total Meets",
                len(filtered_df['meet_number'].unique())
            )
    
    with col3:
        st.metric(
            "Teams",
            len(filtered_df['team_name'].unique())
        )
    
    with col4:
        st.metric(
            "Total Results",
            len(filtered_df)
        )
    
    # Athletes with multiple meets
    athlete_counts = filtered_df.groupby('athlete_full_name').size()
    multi_meet_athletes = athlete_counts[athlete_counts > 1]
    
    st.subheader(f"🎯 Athletes with Progress Data: {len(multi_meet_athletes)}")
    
    # Top performers
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("⚡ Fastest Pace (Normalized)")
        st.caption("Best pace per mile - fair comparison across all divisions")
        # Filter valid paces
        fastest_pace_df = filtered_df[filtered_df['pace_per_mi_min'].notna() & (filtered_df['pace_per_mi_min'] > 0)]
        if len(fastest_pace_df) > 0:
            fastest = fastest_pace_df.nsmallest(10, 'pace_per_mi_min')[[
                'athlete_full_name', 'team_name', 'pace_per_mi_str', 'division', 'meet_name'
            ]]
            fastest.columns = ['Athlete', 'Team', 'Pace/mi', 'Division', 'Meet']
            st.dataframe(fastest, hide_index=True, use_container_width=True)
        else:
            st.info("No pace data available")
    
    with col2:
        st.subheader("🏆 Top Placements")
        top_places = filtered_df[filtered_df['place_overall'] <= 10].sort_values('place_overall')[[
            'athlete_full_name', 'team_name', 'place_overall', 'meet_name'
        ]].head(10)
        top_places.columns = ['Athlete', 'Team', 'Place', 'Meet']
        st.dataframe(top_places, hide_index=True, use_container_width=True)
    
    # Most improved athletes
    if len(multi_meet_athletes) > 0:
        st.subheader("📊 Most Improved Athletes (By Pace)")
        st.caption("Improvement calculated using pace per mile for fair comparison across divisions")
        
        improvements = []
        # Sort athletes by name for consistent ordering
        for athlete in sorted(multi_meet_athletes.index):
            athlete_data = filtered_df[filtered_df['athlete_full_name'] == athlete].sort_values('meet_number')
            if len(athlete_data) >= 2:
                # Use pace per mile for normalization
                first_pace = athlete_data.iloc[0]['pace_per_mi_min']
                last_pace = athlete_data.iloc[-1]['pace_per_mi_min']
                
                if pd.notna(first_pace) and pd.notna(last_pace) and first_pace > 0 and last_pace > 0:
                    # Positive improvement means they got faster (lower pace time)
                    improvement = first_pace - last_pace
                    improvements.append({
                        'Athlete': athlete,
                        'Team': athlete_data.iloc[0]['team_name'],
                        'Pace Improvement (sec/mi)': round(improvement * 60, 1),  # Convert to seconds per mile
                        'First Pace': athlete_data.iloc[0]['pace_per_mi_str'],
                        'Latest Pace': athlete_data.iloc[-1]['pace_per_mi_str'],
                        'Division': athlete_data.iloc[-1]['division']
                    })
        
        if improvements:
            improvements_df = pd.DataFrame(improvements).sort_values('Pace Improvement (sec/mi)', ascending=False)
            st.dataframe(improvements_df.head(10), hide_index=True, use_container_width=True)
        else:
            st.info("No improvement data available for the current selection.")
    
    # Team performance using Cross Country scoring (sum of top 5 places)
    st.subheader("🏫 Team Performance (Cross Country Scoring)")
    
    st.info("**Cross Country Scoring**: Each team's score is the sum of their top 5 finishers' places. Lower score wins!")
    
    # Calculate team scores by season, meet, division, and gender
    team_scores_list = []
    
    # Determine if we're in multi-season mode
    seasons_to_process = filtered_df['season_year'].dropna().unique() if 'season_year' in filtered_df.columns else [None]
    
    for season in seasons_to_process:
        if pd.notna(season):
            season_df = filtered_df[filtered_df['season_year'] == season]
            season_label = int(season)
        else:
            season_df = filtered_df
            season_label = None
            
        for meet in season_df['meet_number'].dropna().unique():
            for division in season_df['division'].dropna().unique():
                for gender in season_df['gender'].dropna().unique():
                    race_df = season_df[
                        (season_df['meet_number'] == meet) &
                        (season_df['division'] == division) &
                        (season_df['gender'] == gender)
                    ].sort_values('place_overall')
                    
                    # Calculate score for each team (sum of top 5 places)
                    for team in race_df['team_name'].dropna().unique():
                        team_runners = race_df[race_df['team_name'] == team].head(5)
                        if len(team_runners) >= 5:  # Only score teams with 5+ runners
                            score = team_runners['place_overall'].sum()
                            avg_time = team_runners['finish_time_s'].mean()
                            
                            score_entry = {
                                'Team': team,
                                'Meet': int(meet),
                                'Division': division,
                                'Gender': gender,
                                'Score': int(score),
                                'Runners': len(team_runners),
                                'Avg Time (s)': round(avg_time, 2) if pd.notna(avg_time) else None
                            }
                            
                            if season_label is not None:
                                score_entry['Season'] = season_label
                                
                            team_scores_list.append(score_entry)
    
    if team_scores_list:
        team_scores_df = pd.DataFrame(team_scores_list)
        
        # Show team scores table sorted by score (lower is better)
        st.subheader("📊 Team Scores by Race")
        
        # Allow filtering by division and gender
        filter_cols = st.columns(3) if 'Season' in team_scores_df.columns else st.columns(2)
        
        if 'Season' in team_scores_df.columns:
            with filter_cols[0]:
                season_filter = st.selectbox("Season", ["All"] + sorted(team_scores_df['Season'].unique().tolist()))
        
        with filter_cols[-2]:
            score_division = st.selectbox("Division", ["All"] + sorted(team_scores_df['Division'].unique().tolist()))
        with filter_cols[-1]:
            score_gender = st.selectbox("Gender", ["All"] + sorted(team_scores_df['Gender'].unique().tolist()))
        
        score_filtered = team_scores_df.copy()
        if 'Season' in team_scores_df.columns and season_filter != "All":
            score_filtered = score_filtered[score_filtered['Season'] == season_filter]
        if score_division != "All":
            score_filtered = score_filtered[score_filtered['Division'] == score_division]
        if score_gender != "All":
            score_filtered = score_filtered[score_filtered['Gender'] == score_gender]
        
        # Sort by season (if applicable), then meet, then score
        sort_cols = ['Season', 'Meet', 'Score'] if 'Season' in score_filtered.columns else ['Meet', 'Score']
        score_filtered = score_filtered.sort_values(sort_cols)
        
        # Format the Season column to remove comma formatting
        if 'Season' in score_filtered.columns:
            score_filtered_display = score_filtered.copy()
            score_filtered_display['Season'] = score_filtered_display['Season'].astype(str)
            st.dataframe(score_filtered_display, hide_index=True, use_container_width=True)
        else:
            st.dataframe(score_filtered, hide_index=True, use_container_width=True)
        
        # Visualization: Best team scores
        st.subheader("🏆 Top Team Performances (Lowest Scores)")
        best_scores = team_scores_df.nsmallest(15, 'Score')
        
        fig_scores = px.bar(
            best_scores,
            x='Team',
            y='Score',
            color='Division',
            hover_data=['Meet', 'Gender', 'Runners'] + (['Season'] if 'Season' in best_scores.columns else []),
            title='Top 15 Team Scores (Lower is Better)',
            labels={'Score': 'Team Score (sum of top 5 places)'}
        )
        
        fig_scores.update_layout(height=500, showlegend=True)
        st.plotly_chart(fig_scores, width='stretch')
    else:
        st.warning("Not enough team data for scoring (teams need 5+ runners)")

# Footer
st.sidebar.markdown("---")
st.sidebar.markdown(f"**Total Results:** {len(df)}")
st.sidebar.markdown(f"**Unique Athletes:** {df['athlete_full_name'].nunique()}")
if 'season_year' in df.columns:
    seasons = sorted([int(y) for y in df['season_year'].dropna().unique()])
    st.sidebar.markdown(f"**Seasons:** {', '.join(map(str, seasons))}")