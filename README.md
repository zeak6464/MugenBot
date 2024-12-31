MUGEN Random AI Battles

This project is a dynamic AI-driven battle manager for the MUGEN fighting game engine. It includes a GUI application for battle control, Twitch bot integration for live interaction, and robust statistics tracking for both characters and stages.

## Features

- **Battle Management**:
  - Single, team, turns, and simultaneous battle modes.
  - Randomized character and stage selection with customizable filters.
  - Real-time battle monitoring and result tracking.

- **GUI Application**:
  - Easy-to-use graphical interface for battle setup and control.
  - Live preview of battles with character and stage stats.
  - Customizable themes and user-friendly settings.

- **Twitch Integration**:
  - Interactive commands for viewers to place bets on battles.
  - Automated points system with real-time updates.
  - Dynamic interaction with live chat.

- **Statistics Tracking**:
  - Character performance stats (wins, losses, win rates).
  - Stage usage stats (times used, total duration, last used).
  - Exportable and resettable statistics.

## Installation

1. **Prerequisites**:
   - Python 3.8 or newer
   - The following Python libraries:
     - `os`
     - `random`
     - `subprocess`
     - `json`
     - `time`
     - `tkinter`
     - `Pillow`
     - `matplotlib`
     - `numpy`
     - `twitchio`
     - `asyncio`

2. **Clone Repository**:
   ```bash
   git clone https://github.com/your-username/random-ai-battles.git
   cd random-ai-battles
   ```

3. **Install Dependencies**:
   Install the required libraries manually or via pip:
   ```bash
   pip install pillow matplotlib numpy twitchio
   ```

4. **Run Application**:
   ```bash
   python random_ai_battles.py
   ```

## Usage

1. Launch the application.
2. Use the GUI to configure battles:
   - Select modes, characters, and stages.
   - Customize settings for rounds, AI levels, and more.
3. Integrate Twitch for live interactions:
   - Set up your bot name and Twitch channel.
   - Enable betting for your audience.
4. Monitor and review battle results in real-time.

## Twitch Commands

- `!bet [team] [amount]`: Place a bet on Team 1 or Team 2.
- `!points`: Check your current points.
- `!help`: Display the list of available commands.
- `!stats`: Show the stats for the current battle.

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork this repository.
2. Create a branch (`git checkout -b feature/new-feature`).
3. Commit your changes (`git commit -am 'Add new feature'`).
4. Push to the branch (`git push origin feature/new-feature`).
5. Open a Pull Request.

## License

This project is licensed under the [MIT License](LICENSE).

