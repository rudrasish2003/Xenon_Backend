import os
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Response
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from bson import ObjectId, Binary
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Literal
import uvicorn
from dotenv import load_dotenv

app = FastAPI()

load_dotenv()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.getenv("MONGO_URI")
client = AsyncIOMotorClient(MONGO_URI)
db = client.player_db

collection = db.players
tournaments_collection = db.tournaments
teams_collection = db.teams
matches_collection = db.matches

# --- Helper: Player Serializer ---
def player_helper(player) -> dict:
    matches = player.get("matches_played", 0)
    wins = player.get("wins", 0)
    draws = player.get("draws", 0)
    loss = player.get("loss", 0)

    if matches > 0:
        non_losing_games = wins + draws
        unbeaten_pct = round((non_losing_games / matches) * 100, 2)
    else:
        unbeaten_pct = 0.0

    return {
        "id": str(player["_id"]),
        "name": player["name"],
        "dob": player["dob"],
        "instagram_link": player.get("instagram_link"),
        "facebook_link": player.get("facebook_link"),
        "photo_url": f"/players/{str(player['_id'])}/photo",
        "matches_played": matches,
        "wins": wins,
        "draws": draws,
        "loss": loss,
        "unbeaten_percentage": unbeaten_pct
    }

# --- Pydantic Models ---
class PlayerUpdate(BaseModel):
    name: Optional[str] = None
    dob: Optional[str] = None
    instagram_link: Optional[str] = None
    facebook_link: Optional[str] = None

class TournamentCreate(BaseModel):
    name: str
    total_teams: int

class TeamCreate(BaseModel):
    name: str
    tournament_id: str
    player_ids: List[str]

class TeamUpdate(BaseModel):
    name: str

class PlayerMatchResult(BaseModel):
    player_id: str
    result: Literal['win', 'loss', 'draw', 'sub']

class MatchCreate(BaseModel):
    tournament_id: str
    team_id: str
    opponent_name: str
    player_results: List[PlayerMatchResult]

# --- Helper: Rollback Stats Logic ---
async def rollback_match_stats(match_doc):
    """
    Reverses the stats effects of a match.
    Subtracts points/wins/losses from Team and Players.
    """
    team_id = match_doc["team_id"]
    team_result = match_doc.get("calculated_team_result", "draw")
    player_results = match_doc.get("player_results", [])
    
    # 1. Reverse Team Stats
    team_inc = {"stats.matches_played": -1}
    if team_result == "win":
        team_inc["stats.wins"] = -1
        team_inc["stats.points"] = -3
    elif team_result == "loss":
        team_inc["stats.loss"] = -1
    else: # draw
        team_inc["stats.draws"] = -1
        team_inc["stats.points"] = -1
        
    await teams_collection.update_one({"_id": ObjectId(team_id)}, {"$inc": team_inc})

    # 2. Reverse Player Stats
    for p in player_results:
        pid = p["player_id"]
        res = p["result"]
        if res == "sub": continue
        
        # Global
        g_inc = {"matches_played": -1}
        if res == "win": g_inc["wins"] = -1
        elif res == "loss": g_inc["loss"] = -1
        elif res == "draw": g_inc["draws"] = -1
        await collection.update_one({"_id": ObjectId(pid)}, {"$inc": g_inc})

        # Tournament
        t_inc = {"players.$.stats.matches_played": -1}
        if res == "win": t_inc["players.$.stats.wins"] = -1
        elif res == "loss": t_inc["players.$.stats.loss"] = -1
        elif res == "draw": t_inc["players.$.stats.draws"] = -1
        
        await teams_collection.update_one(
            {"_id": ObjectId(team_id), "players.player_id": pid},
            {"$inc": t_inc}
        )

# --- ROUTES ---

@app.get("/")
async def root():
    return {"message": "API Running"}

# ... (Include your existing PLAYER routes here: add_player, get_all_players, etc.) ...
# [Paste previous Player Routes here if needed, omitting for brevity as they haven't changed]
@app.post("/players/")
async def add_player(name: str = Form(...), dob: str = Form(...), instagram_link: str = Form(...), facebook_link: str = Form(...), photo: UploadFile = File(...)):
    photo_content = await photo.read()
    player_data = {"name": name, "dob": dob, "instagram_link": instagram_link, "facebook_link": facebook_link, "photo_data": Binary(photo_content), "photo_content_type": photo.content_type, "matches_played": 0, "wins": 0, "draws": 0, "loss": 0}
    new_player = await collection.insert_one(player_data)
    return player_helper(await collection.find_one({"_id": new_player.inserted_id}))

@app.get("/players/")
async def get_all_players():
    players = []
    async for player in collection.find(): players.append(player_helper(player))
    return players

@app.get("/players/{id}")
async def get_player(id: str):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    p = await collection.find_one({"_id": ObjectId(id)})
    if p: return player_helper(p)
    raise HTTPException(404, "Not Found")

@app.get("/players/{id}/photo")
async def get_player_photo(id: str):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    p = await collection.find_one({"_id": ObjectId(id)})
    if p and "photo_data" in p: return Response(content=p["photo_data"], media_type=p["photo_content_type"])
    raise HTTPException(404, "Photo not found")

@app.put("/players/{id}")
async def update_player(id: str, name: Optional[str] = Form(None), dob: Optional[str] = Form(None), instagram_link: Optional[str] = Form(None), facebook_link: Optional[str] = Form(None), photo: Optional[UploadFile] = File(None)):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    update_data = {}
    if name: update_data["name"] = name
    if dob: update_data["dob"] = dob
    if instagram_link: update_data["instagram_link"] = instagram_link
    if facebook_link: update_data["facebook_link"] = facebook_link
    if photo:
        content = await photo.read()
        update_data["photo_data"] = Binary(content)
        update_data["photo_content_type"] = photo.content_type
    if not update_data: raise HTTPException(400, "No data")
    await collection.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    return player_helper(await collection.find_one({"_id": ObjectId(id)}))

@app.delete("/players/{id}")
async def delete_player(id: str):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    res = await collection.delete_one({"_id": ObjectId(id)})
    if res.deleted_count == 1: return {"message": "Deleted"}
    raise HTTPException(404, "Not Found")


# ===========================
#    TOURNAMENT MANAGEMENT
# ===========================

@app.post("/tournaments/")
async def create_tournament(tournament: TournamentCreate):
    t_data = tournament.dict()
    new_t = await tournaments_collection.insert_one(t_data)
    return {"message": "Tournament created", "id": str(new_t.inserted_id)}

@app.get("/tournaments/")
async def get_tournaments():
    tournaments = []
    async for t in tournaments_collection.find():
        t["id"] = str(t["_id"])
        del t["_id"]
        tournaments.append(t)
    return tournaments


# ===========================
#      TEAM MANAGEMENT
# ===========================

@app.post("/teams/")
async def add_team(team: TeamCreate):
    if not await tournaments_collection.find_one({"_id": ObjectId(team.tournament_id)}):
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    team_players = []
    for pid in team.player_ids:
        p = await collection.find_one({"_id": ObjectId(pid)})
        if not p: raise HTTPException(status_code=400, detail=f"Invalid Player ID: {pid}")
        team_players.append({
            "player_id": str(p["_id"]),
            "name": p["name"],
            "stats": {"matches_played": 0, "wins": 0, "draws": 0, "loss": 0}
        })

    team_data = {
        "name": team.name,
        "tournament_id": team.tournament_id,
        "stats": {"matches_played": 0, "wins": 0, "draws": 0, "loss": 0, "points": 0},
        "players": team_players
    }
    new_team = await teams_collection.insert_one(team_data)
    return {"message": "Team added", "id": str(new_team.inserted_id)}

@app.get("/tournaments/{tournament_id}/teams")
async def get_tournament_teams(tournament_id: str):
    teams = []
    async for team in teams_collection.find({"tournament_id": tournament_id}):
        team["id"] = str(team["_id"])
        del team["_id"]
        teams.append(team)
    return teams

@app.delete("/teams/{id}")
async def delete_team(id: str):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    # Note: Deleting a team does NOT rollback stats from matches already played.
    # It just removes the team from the tournament list.
    res = await teams_collection.delete_one({"_id": ObjectId(id)})
    if res.deleted_count == 1: return {"message": "Team deleted"}
    raise HTTPException(404, "Team not found")

@app.put("/teams/{id}")
async def update_team(id: str, team: TeamUpdate):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    res = await teams_collection.update_one(
        {"_id": ObjectId(id)}, 
        {"$set": {"name": team.name}}
    )
    if res.modified_count == 1: return {"message": "Team updated"}
    raise HTTPException(404, "Team not found or no changes")


# ===========================
#      MATCH MANAGEMENT
# ===========================

@app.get("/tournaments/{tournament_id}/matches")
async def get_tournament_matches(tournament_id: str):
    matches = []
    async for m in matches_collection.find({"tournament_id": tournament_id}):
        m["id"] = str(m["_id"])
        del m["_id"]
        # Fetch team name for display
        team = await teams_collection.find_one({"_id": ObjectId(m["team_id"])})
        m["team_name"] = team["name"] if team else "Unknown Team"
        matches.append(m)
    return matches

@app.post("/matches/")
async def record_match(match: MatchCreate):
    # A. Calculate Team Outcome
    match_wins = 0
    match_losses = 0
    for p in match.player_results:
        if p.result == 'win': match_wins += 1
        elif p.result == 'loss': match_losses += 1
            
    team_result = "draw"
    points_awarded = 1
    if match_wins > match_losses:
        team_result = "win"
        points_awarded = 3
    elif match_losses > match_wins:
        team_result = "loss"
        points_awarded = 0

    # B. Save Match Record
    match_doc = match.dict()
    match_doc["calculated_team_result"] = team_result
    new_match = await matches_collection.insert_one(match_doc)

    # C. Update TEAM Stats
    team_update_fields = {"stats.matches_played": 1, "stats.points": points_awarded}
    if team_result == "win": team_update_fields["stats.wins"] = 1
    elif team_result == "loss": team_update_fields["stats.loss"] = 1
    else: team_update_fields["stats.draws"] = 1

    await teams_collection.update_one({"_id": ObjectId(match.team_id)}, {"$inc": team_update_fields})

    # D. Update PLAYER Stats
    for p_res in match.player_results:
        pid = p_res.player_id
        res = p_res.result
        if res == "sub": continue

        # Global
        global_inc = {"matches_played": 1}
        if res == "win": global_inc["wins"] = 1
        elif res == "loss": global_inc["loss"] = 1
        elif res == "draw": global_inc["draws"] = 1
        await collection.update_one({"_id": ObjectId(pid)}, {"$inc": global_inc})

        # Tournament
        tourney_inc = {"players.$.stats.matches_played": 1}
        if res == "win": tourney_inc["players.$.stats.wins"] = 1
        elif res == "loss": tourney_inc["players.$.stats.loss"] = 1
        elif res == "draw": tourney_inc["players.$.stats.draws"] = 1
        await teams_collection.update_one(
            {"_id": ObjectId(match.team_id), "players.player_id": pid},
            {"$inc": tourney_inc}
        )

    return {"message": "Match recorded", "team_result": team_result}

@app.delete("/matches/{id}")
async def delete_match(id: str):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    
    # 1. Fetch match to know what to rollback
    match = await matches_collection.find_one({"_id": ObjectId(id)})
    if not match: raise HTTPException(404, "Match not found")
    
    # 2. Rollback Stats
    await rollback_match_stats(match)
    
    # 3. Delete Document
    await matches_collection.delete_one({"_id": ObjectId(id)})
    
    return {"message": "Match deleted and stats rolled back"}

@app.put("/matches/{id}")
async def update_match(id: str, match: MatchCreate):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    
    # 1. Fetch old match
    old_match = await matches_collection.find_one({"_id": ObjectId(id)})
    if not old_match: raise HTTPException(404, "Match not found")
    
    # 2. Rollback old stats
    await rollback_match_stats(old_match)
    
    # 3. Delete old match record (effectively, we replace it)
    await matches_collection.delete_one({"_id": ObjectId(id)})
    
    # 4. Record new match (This re-applies the new stats)
    # We call the logic directly to avoid another HTTP request, 
    # but for simplicity in this script, we can reuse the logic block.
    # Ideally, refactor record_match logic into a function `process_match_stats`.
    # For now, we will just call the same logic as record_match but return the ID.
    
    # ... (Re-run record match logic) ...
    # Copy-paste the logic from record_match OR refactor. 
    # For this snippet, I will just call the function logic:
    
    match_wins = 0
    match_losses = 0
    for p in match.player_results:
        if p.result == 'win': match_wins += 1
        elif p.result == 'loss': match_losses += 1
    
    team_result = "draw"
    points_awarded = 1
    if match_wins > match_losses:
        team_result = "win"
        points_awarded = 3
    elif match_losses > match_wins:
        team_result = "loss"
        points_awarded = 0

    match_doc = match.dict()
    match_doc["calculated_team_result"] = team_result
    # Use the OLD ID to keep the same URL/ID
    match_doc["_id"] = ObjectId(id) 
    await matches_collection.insert_one(match_doc)

    # Update Team Stats (New)
    team_update_fields = {"stats.matches_played": 1, "stats.points": points_awarded}
    if team_result == "win": team_update_fields["stats.wins"] = 1
    elif team_result == "loss": team_update_fields["stats.loss"] = 1
    else: team_update_fields["stats.draws"] = 1

    await teams_collection.update_one({"_id": ObjectId(match.team_id)}, {"$inc": team_update_fields})

    # Update Player Stats (New)
    for p_res in match.player_results:
        pid = p_res.player_id
        res = p_res.result
        if res == "sub": continue

        global_inc = {"matches_played": 1}
        if res == "win": global_inc["wins"] = 1
        elif res == "loss": global_inc["loss"] = 1
        elif res == "draw": global_inc["draws"] = 1
        await collection.update_one({"_id": ObjectId(pid)}, {"$inc": global_inc})

        tourney_inc = {"players.$.stats.matches_played": 1}
        if res == "win": tourney_inc["players.$.stats.wins"] = 1
        elif res == "loss": tourney_inc["players.$.stats.loss"] = 1
        elif res == "draw": tourney_inc["players.$.stats.draws"] = 1
        await teams_collection.update_one(
            {"_id": ObjectId(match.team_id), "players.player_id": pid},
            {"$inc": tourney_inc}
        )

    return {"message": "Match updated successfully"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)