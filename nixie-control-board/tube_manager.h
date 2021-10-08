#ifndef TUBE_MANAGER_H
#define TUBE_MANAGER_H

#define NUM_TUBES 12
#define CMD_BUF_SIZE 128

#define TUBE_OK 0
#define TUBE_ERROR_OTHER -1
#define TUBE_ERR_BUF_OVERRUN -2
#define TUBE_ERR_BAD_CMD -3
#define TUBE_ERR_CMD_TOO_LONG -4
#define TUBE_ERR_CMD_NOOP -5

#define TUBE_CMD_PRINT 1

int buildCmd(char* new_cmd, int len);
int commandSize(void);
int noopCommand(void);
int commandComplete(void);
int cmdBufLen(void);
int getCmd(char* buf, int buf_len);
int cmdType(char* buf);
int cmdArgStart(char* buf, int len);
int cmdDecodePrint(char* buf, int buf_len, uint16_t* tube_bitmap, int bitmap_len);
const char* tubeErrToText(int errcode);


#endif // TUBE_MANAGER_H
