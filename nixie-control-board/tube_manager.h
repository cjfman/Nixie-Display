#ifndef TUBE_MANAGER_H
#define TUBE_MANAGER_H

#define NUM_TUBES 12
#define CMD_BUF_SIZE 128
#define CMD_MAX_NUM_ARGS 10

#define TUBE_OK 0
#define TUBE_ERROR_OTHER -1
#define TUBE_ERR_BUF_OVERRUN -2
#define TUBE_ERR_BAD_CMD -3
#define TUBE_ERR_CMD_TOO_LONG -4
#define TUBE_ERR_CMD_NOOP -5
#define TUBE_ERR_TOO_MANY_ARGS -6
#define TUBE_ERR_WRONG_NUM_ARGS -7

#define TUBE_CMD_PRINT 1

typedef enum CommandType {
    Print,
    Noop
} CommandType;

typedef struct Command {
    char* buf;
    CommandType type;
    int numargs;
    char* args[CMD_MAX_NUM_ARGS];
} Command;

void clearCache(void);
int buildCmd(char* new_cmd, int len);
int commandSize(void);
int noopCommand(void);
int commandComplete(void);
int cmdBufLen(void);
int getCmd(char* buf, int buf_len);
int cmdParse(Command* cmd, char* buf, int len);
int cmdDecodePrint(char* buf, uint16_t* tube_bitmap, int bitmap_len);
const char* tubeErrToText(int errcode);


#endif // TUBE_MANAGER_H
