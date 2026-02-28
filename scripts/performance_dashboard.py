"""
Performance tracking dashboard components for Streamlit app.
"""
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from scripts.pick_tracker import PickTracker


def render_performance_summary(tracker: PickTracker):
    """Render high-level performance metrics."""
    import streamlit as st
    
    summary = tracker.get_performance_summary()
    
    if not summary or summary['total_picks'] == 0:
        st.info("No completed picks yet. Check back after games finish!")
        return
    
    # Top-line metrics
    col1, col2, col3, col4 = st.columns(4)
    
    col1.metric(
        "Total Picks",
        summary['total_picks'],
        f"{summary['wins']}-{summary['losses']}-{summary['pushes']}"
    )
    
    col2.metric(
        "Win Rate",
        f"{summary['win_rate']:.1%}",
        help="Wins / (Wins + Losses), excluding pushes"
    )
    
    profit_color = "normal" if summary['total_profit'] >= 0 else "inverse"
    col3.metric(
        "Total Profit",
        f"{summary['total_profit']:+.2f}u",
        delta_color=profit_color
    )
    
    roi_color = "normal" if summary['roi'] >= 0 else "inverse"
    col4.metric(
        "ROI per Pick",
        f"{summary['roi']:+.2f}u",
        delta_color=roi_color
    )


def render_pick_history(tracker: PickTracker):
    """Render detailed pick history table."""
    import streamlit as st
    import sqlite3
    
    conn = sqlite3.connect(tracker.db_path)
    
    df = pd.read_sql_query("""
        SELECT 
            date,
            game,
            bet_type,
            bet_side,
            line,
            odds,
            model_edge,
            safety_score,
            result,
            profit
        FROM picks
        ORDER BY date DESC, created_at DESC
        LIMIT 100
    """, conn)
    
    conn.close()
    
    if df.empty:
        st.info("No picks logged yet")
        return
    
    # Format columns
    df['edge'] = df['model_edge'].apply(lambda x: f"{x:+.1%}")
    df['safety'] = df['safety_score'].apply(lambda x: f"{x:.2f}")
    df['line'] = df['line'].apply(lambda x: f"{x:+.1f}" if pd.notna(x) else "ML")
    df['odds'] = df['odds'].apply(lambda x: f"{x:+d}" if pd.notna(x) else "—")
    df['profit'] = df['profit'].apply(lambda x: f"{x:+.2f}u" if pd.notna(x) else "—")
    
    # Status emoji
    status_map = {'win': '✅', 'loss': '❌', 'push': '🟡', 'pending': '⏳'}
    df['status'] = df['result'].map(status_map)
    
    # Display table
    display_df = df[[
        'date', 'game', 'bet_type', 'bet_side', 'line', 'odds',
        'edge', 'safety', 'status', 'profit'
    ]]
    
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_performance_charts(tracker: PickTracker):
    """Render performance visualization charts."""
    import streamlit as st
    import sqlite3
    
    conn = sqlite3.connect(tracker.db_path)
    
    df = pd.read_sql_query("""
        SELECT 
            date,
            bet_type,
            result,
            profit,
            model_edge,
            safety_score
        FROM picks
        WHERE result IS NOT NULL AND result != 'pending'
        ORDER BY date
    """, conn)
    
    conn.close()
    
    if df.empty:
        st.info("No completed picks for charting yet")
        return
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Cumulative Profit")
        
        df['cumulative_profit'] = df['profit'].cumsum()
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=list(range(1, len(df) + 1)),
            y=df['cumulative_profit'],
            mode='lines+markers',
            name='Profit',
            line=dict(color='#2ecc71', width=2),
            fill='tozeroy',
            fillcolor='rgba(46, 204, 113, 0.1)'
        ))
        
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        
        fig.update_layout(
            xaxis_title="Pick Number",
            yaxis_title="Units",
            template="plotly_dark",
            height=300,
            margin=dict(t=20, b=40),
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.subheader("Win Rate by Bet Type")
        
        type_summary = df.groupby('bet_type').agg({
            'result': lambda x: (x == 'win').sum() / ((x == 'win').sum() + (x == 'loss').sum()),
            'profit': 'sum'
        }).reset_index()
        
        type_summary.columns = ['bet_type', 'win_rate', 'profit']
        
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=type_summary['bet_type'],
            y=type_summary['win_rate'] * 100,
            text=type_summary['win_rate'].apply(lambda x: f"{x:.1%}"),
            textposition='outside',
            marker_color=['#3498db', '#e74c3c', '#f39c12'][:len(type_summary)]
        ))
        
        fig.update_layout(
            yaxis_title="Win Rate (%)",
            yaxis_range=[0, 100],
            template="plotly_dark",
            height=300,
            margin=dict(t=20, b=40),
            showlegend=False
        )
        
        st.plotly_chart(fig, use_container_width=True)


def render_edge_validation(tracker: PickTracker):
    """Compare model edge vs actual results - the self-improvement core."""
    import streamlit as st
    import sqlite3
    
    st.subheader("Model Calibration")
    st.caption("Does our edge prediction match reality?")
    
    conn = sqlite3.connect(tracker.db_path)
    
    df = pd.read_sql_query("""
        SELECT 
            model_edge,
            model_win_prob,
            result
        FROM picks
        WHERE result IN ('win', 'loss')
    """, conn)
    
    conn.close()
    
    if len(df) < 10:
        st.info("Need at least 10 completed picks for calibration analysis")
        return
    
    # Bin by predicted probability
    df['prob_bucket'] = pd.cut(df['model_win_prob'], bins=[0, 0.5, 0.6, 0.7, 0.8, 1.0], 
                                labels=['40-50%', '50-60%', '60-70%', '70-80%', '80%+'])
    
    calibration = df.groupby('prob_bucket').agg({
        'result': lambda x: (x == 'win').mean(),
        'model_win_prob': 'mean'
    }).reset_index()
    
    calibration.columns = ['bucket', 'actual_win_rate', 'predicted_win_rate']
    
    fig = go.Figure()
    
    # Perfect calibration line
    fig.add_trace(go.Scatter(
        x=[0, 1], y=[0, 1],
        mode='lines',
        name='Perfect Calibration',
        line=dict(color='gray', dash='dash')
    ))
    
    # Actual calibration
    fig.add_trace(go.Scatter(
        x=calibration['predicted_win_rate'],
        y=calibration['actual_win_rate'],
        mode='markers+lines',
        name='Model Performance',
        marker=dict(size=12, color='#3498db'),
        line=dict(color='#3498db', width=2)
    ))
    
    fig.update_layout(
        xaxis_title="Predicted Win Rate",
        yaxis_title="Actual Win Rate",
        template="plotly_dark",
        height=400,
        xaxis_range=[0, 1],
        yaxis_range=[0, 1]
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    st.caption("If model is well-calibrated, points should follow the diagonal line. "
              "Above = overconfident. Below = underconfident.")
