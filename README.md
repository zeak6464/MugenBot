# MUGEN Random AI Battles

A comprehensive battle management system for MUGEN fighting game engine, featuring an advanced GUI, tournament system, and Twitch integration. This project enables automated AI battles with extensive tracking and management capabilities.

## Features

### Battle Management
- **Multiple Battle Modes**:
  - Single battles (1v1)
  - Simultaneous team battles
  - Tournament system with bracket management
  - Customizable AI difficulty levels

- **Character Management**:
  - Random or manual character selection
  - Character tier tracking based on performance
  - Win/loss statistics tracking
  - Character filtering and organization

- **Stage Management**:
  - Random or specific stage selection
  - Stage usage statistics
  - Customizable stage pools for tournaments

### Tournament System
- **Tournament Formats**:
  - Single Elimination brackets
  - Automatic bracket generation and management
  - Real-time tournament progress tracking
  - Visual bracket display
  - Configurable tournament sizes (4, 8, 16, 32 players)

### GUI Features
- **Modern Interface**:
  - Tabbed interface with draggable tabs
  - Real-time battle preview
  - Live statistics updates
  - Tournament bracket visualization

- **Battle Statistics**:
  - Detailed character performance tracking
  - Win rates and matchup statistics
  - Exportable statistics in CSV format
  - Visual data representations

### Twitch Integration
- **Interactive Features**:
  - Viewer betting system
  - Real-time point tracking
  - Interactive chat commands
  - Battle result announcements

## Installation

1. **Prerequisites**:
   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Required Python Libraries**:
   - tkinter (GUI)
   - Pillow (Image handling)
   - matplotlib (Statistics visualization)
   - numpy (Data processing)
   - twitchio (Twitch integration)
   - asyncio (Async operations)

3. **Setup**:
   ```bash
   git clone https://github.com/yourusername/mugen-random-battles.git
   cd mugen-random-battles
   python random_ai_battles.py
   ```

## Usage

### Basic Battle Mode
1. Launch the application
2. Select the "Battle" tab
3. Choose battle mode (single/simul)
4. Configure AI settings
5. Start battle

### Tournament Mode
1. Select the "Tournament" tab
2. Choose tournament size
3. Select participating characters
4. Choose stage pool
5. Start tournament
6. Monitor progress in bracket display

### Twitch Integration
Available commands:
- `!bet [team] [amount]` - Place bet on current match
- `!points` - Check points balance
- `!stats` - View current battle/tournament statistics
- `!help` - Display available commands

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit changes (`git commit -m 'Add AmazingFeature'`)
4. Push to branch (`git push origin feature/AmazingFeature`)
5. Open Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

