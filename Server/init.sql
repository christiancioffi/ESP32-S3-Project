USE TesiDB;
CREATE TABLE IF NOT EXISTS AudioChunks (
    ID INT AUTO_INCREMENT PRIMARY KEY,
    filename VARCHAR(100) NOT NULL,
    timestamp DATETIME NOT NULL,
    battery_level INT NOT NULL,
    node_id VARCHAR(100) NOT NULL,
    rms FLOAT NOT NULL
);

CREATE INDEX idx_timestamp ON AudioChunks (timestamp);

CREATE TABLE IF NOT EXISTS Events (
    ID INT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    timestamp DATETIME NOT NULL
);

